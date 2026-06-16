# Python base
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps & Chrome headless runtime libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip ca-certificates \
    fonts-liberation libasound2 libatk-bridge2.0-0 libnss3 libgbm1 \
    libx11-6 libxcomposite1 libxcursor1 libxdamage1 libxfixes3 libxi6 \
    libxrandr2 libxrender1 libxss1 libxtst6 libglib2.0-0 libgtk-3-0 libgdk-pixbuf-2.0-0 \
  && rm -rf /var/lib/apt/lists/*

# Install Chrome for Testing + matching Chromedriver
# Reference: https://googlechromelabs.github.io/chrome-for-testing/
ARG CFT_CHANNEL=Stable
RUN set -eux; \
  CFT_VERSION="$(curl -fsSL https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json \
    | python -c "import sys,json; print(json.load(sys.stdin)['channels']['${CFT_CHANNEL}']['version'])")"; \
  BASE_URL="https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/${CFT_VERSION}/linux64"; \
  curl -fsSL -o /tmp/chrome.zip      "${BASE_URL}/chrome-linux64.zip"; \
  curl -fsSL -o /tmp/chromedriver.zip "${BASE_URL}/chromedriver-linux64.zip"; \
  unzip /tmp/chrome.zip -d /opt; \
  unzip /tmp/chromedriver.zip -d /opt; \
  mv /opt/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver; \
  ln -s /opt/chrome-linux64/chrome /usr/bin/google-chrome; \
  rm -rf /tmp/*.zip /opt/chromedriver-linux64; \
  chmod +x /usr/local/bin/chromedriver /usr/bin/google-chrome

# Copy requirements first (better layer caching)
COPY requirements.txt .

# Install Python deps
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code
COPY . .

# (Optional) tell Selenium where Chrome lives; the symlink also works
ENV CHROME_BIN=/opt/chrome-linux64/chrome

# Run your package
CMD ["python", "-m", "mbdataflow"]
