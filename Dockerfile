# ============================================================
# MBDataFlow_ETL — Cloud Run Job image
# Build: gcloud builds submit --tag <IMAGE> .
# ============================================================

FROM python:3.13-slim

# Prevent interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive \
    # Don't write .pyc files to disk
    PYTHONDONTWRITEBYTECODE=1 \
    # Force stdout/stderr to be unbuffered (critical for Cloud Logging)
    PYTHONUNBUFFERED=1 \
    # Tell Selenium where Chrome lives
    CHROME_BIN=/opt/chrome-linux64/chrome

WORKDIR /app

# ── System dependencies ───────────────────────────────────
# curl/unzip: needed to fetch Chrome for Testing
# ca-certificates: HTTPS
# Rest: Chrome runtime shared libraries on Debian slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libnss3 \
    libgbm1 \
    libx11-6 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    libglib2.0-0 \
    libgtk-3-0 \
    libgdk-pixbuf-2.0-0 \
  && rm -rf /var/lib/apt/lists/*

# ── Chrome for Testing + ChromeDriver ────────────────────
# Fetches latest Stable versions from Google's CFT API.
# Chrome and ChromeDriver versions are always in sync this way.
RUN set -eux; \
  CFT_VERSION="$(curl -fsSL \
    https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json \
    | python -c "import sys,json; print(json.load(sys.stdin)['channels']['Stable']['version'])")"; \
  BASE_URL="https://storage.googleapis.com/chrome-for-testing-public/${CFT_VERSION}/linux64"; \
  curl -fsSL -o /tmp/chrome.zip       "${BASE_URL}/chrome-linux64.zip"; \
  curl -fsSL -o /tmp/chromedriver.zip "${BASE_URL}/chromedriver-linux64.zip"; \
  unzip /tmp/chrome.zip      -d /opt; \
  unzip /tmp/chromedriver.zip -d /opt; \
  mv /opt/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver; \
  ln -s /opt/chrome-linux64/chrome /usr/bin/google-chrome; \
  rm -rf /tmp/*.zip /opt/chromedriver-linux64; \
  chmod +x /usr/local/bin/chromedriver /usr/bin/google-chrome

# ── Python dependencies ───────────────────────────────────
# Copied first to leverage Docker layer caching:
# if requirements.txt didn't change, this layer is reused.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────
COPY config/         ./config/
COPY extract/        ./extract/
COPY load/           ./load/
COPY transform/      ./transform/
COPY pipelines/      ./pipelines/
COPY utils/          ./utils/

# ── Default entrypoint ────────────────────────────────────
# Cloud Run Job overrides this via --args at deploy time.
# Each pipeline gets its own Job pointing to its own module.
CMD ["python", "-m", "pipelines.pipeline_Desinc"]