# Architecture

Estado del proyecto **MBDataFlow_ETL** y decisiones de diseño tomadas hasta la fecha. Documento vivo: actualizar cuando una decisión nueva afecte la estructura del sistema.

---

## 1. Propósito

Monorepo de pipelines ETL/EL para datos operativos de Metrobús CDMX. Cada pipeline extrae datos de una fuente operativa (plataforma Sonda Sinóptico Plus, archivos en Drive, etc.), opcionalmente los transforma, y los carga a Google Drive y/o BigQuery. Despliegue como Cloud Run Jobs en GCP, programados con Cloud Scheduler.

---

## 2. Estado de pipelines

| Pipeline | Tipo | Estado | Trigger |
|---|---|---|---|
| `pipeline_Desinc` | EL | ✅ Producción | Cloud Scheduler diario · 5:00 AM CDMX |
| `pipeline_Circuitos` | EL | ✅ Producción | Cloud Scheduler semanal · 7:00 AM CDMX|
| `pipeline_CanBus` | EL | 🧊 Pausado · calidad de datos upstream | — |
| `pipeline_rangofechas_canbus` | EL | 🧊 Pausado · calidad de datos upstream | — |
| Otros (`pipeline_Viaje`, `pipeline_ReporteOp`) | En desarrollo | 🚧 | — |

---

## 3. Stack

- **Lenguaje:** Python 3.13
- **Cloud:** GCP — Cloud Run Jobs, Cloud Build, Cloud Scheduler, Cloud Monitoring, Artifact Registry, Secret Manager
- **Storage:** Google Drive (vía `google-api-python-client`), BigQuery (próximamente)
- **Scraping:** Selenium 4 con Chrome for Testing
- **Auth:** Service Account única (`mbdataflow-runner`) con ADC
- **Config:** Variables de entorno + `python-dotenv` local, Secret Manager en producción
- **Control de versiones:** GitHub (cuenta personal `quetzalcxatl`) · repo `mbdataflow-etl`
- **Dependencias:** `requirements.txt` con versiones fijas (`pip freeze` desde venv limpio)

---

## 4. Estructura del repo

```
MBDataFlow_ETL/
├── config/
│   ├── settings.py              # env vars + _runtime_path() helper
│   └── credentials/             # gitignored
├── extract/
│   ├── base.py                  # Extractor ABC
│   ├── helpers/
│   └── scrapers/
│       └── Desincorporaciones.py
|       └── Circuitos.py   
├── transform/
│   └── transformers/
├── load/
│   ├── base.py                  # Loader ABC
│   └── loaders/
│       └── Desincorporaciones_drive_loader.py
│       └── Circuitos_drive_loader.py
├── pipelines/
│   ├── pipeline_Desinc.py       # entrypoints invocables como python -m
│   ├── pipeline_Circuitos.py
│   ├── pipeline_CanBus.py                  # en pausa
│   └── pipeline_rangofechas_canbus.py      # en pausa
├── utils/
│   ├── dates.py                 # yesterday_cdmx(), today_cdmx() · TZ-aware
│   ├── logger.py
│   └── turno.py
├── scripts/
│   ├── deploy_desinc.ps1        # build + update job
│   ├── deploy_job_desinc.ps1    # crear job (one-time)
│   ├── setup_scheduler_desinc.ps1   
|   ├── deploy_circuitos.ps1         
│   ├── deploy_job_circuitos.ps1     
│   └── setup_scheduler_circuitos.ps1
├── docs/
│   ├── architecture.html        # diagrama visual
│   └── monitoring.md
├── tests/                        # vacío
├── Dockerfile                    # Python 3.13 + Chrome for Testing
├── cloudbuild.yaml
├── requirements.txt              # versiones pineadas
├── .env.example                  # template
└── .env                          # gitignored
```

---

## 5. Decisiones de diseño

### 5.1 Monorepo con imagen Docker compartida

- Una sola imagen para todos los pipelines.
- Cada pipeline se ejecuta como `python -m pipelines.X` desde Cloud Run Jobs.
- Cada pipeline tiene un Cloud Run Job propio: `pipeline-desinc`, `pipeline-circ`, etc.

**Razón:** mantenimiento simple (un solo Dockerfile, un solo `requirements.txt`), build cache compartido. Cuando un pipeline necesite stack radicalmente distinto (e.g. uno sin Selenium), se reconsidera.

### 5.2 Configuración por env vars, secretos por Secret Manager

- `config/settings.py` lee `os.environ.get(...)` para toda variable ambiente-específica.
- Variables sensibles (passwords, credenciales) viven en Secret Manager y se inyectan al Job con `--set-secrets`.
- Variables no sensibles (project ID, folder IDs) van como `--set-env-vars`.
- `.env` local con `python-dotenv` para desarrollo. **Nunca commiteado.**

### 5.3 Auth a Google APIs por Service Account con ADC

- SA única: `mbdataflow-runner@<project>.iam.gserviceaccount.com`.
- Permisos a nivel proyecto: `logging.logWriter`, `secretmanager.secretAccessor`, `run.invoker`.
- Acceso a Drive: folder compartido directamente con el email de la SA (Editor).
- Código usa `google.auth.default()` — funciona transparentemente en local (con `GOOGLE_APPLICATION_CREDENTIALS` apuntando a la SA key) y en Cloud Run (con la SA attached al Job).

**Razón:** OAuth de usuario requiere navegador, incompatible con Cloud Run. SA con ADC es el patrón estándar de GCP.

### 5.4 Detección centralizada de ambiente

`config/settings.py` expone `_runtime_path(local_path)` que devuelve `/tmp` si detecta Cloud Run (`CLOUD_RUN_JOB`, `K_SERVICE`, `CLOUD_RUN_EXECUTION` en `os.environ`) o el path local en caso contrario.

Todos los `RAW_*_PATH` deben envolverse con esta función para que el scraper y el loader miren el mismo directorio en ambos ambientes. **Crítico:** el contenedor de Cloud Run solo permite escritura en `/tmp`.

### 5.5 Fechas timezone-aware en CDMX

`utils/dates.py` expone `yesterday_cdmx()`, `today_cdmx()` y `last_completed_week_cdmx()` (retorna tupla `(monday, sunday)` de la última semana completa estrictamente anterior a hoy). Cualquier referencia a "el día actual", "ayer" o "la semana vencida" en el código debe usar estas funciones — nunca `datetime.now()` directo.

**Razón:** `datetime.now()` naive devuelve la hora local del sistema; en Cloud Run es UTC, en local es CDMX. Para pipelines que procesan datos "del día anterior", esto produce bugs sutiles cuando se ejecuta cerca de medianoche UTC.

### 5.6 Propagación de fallos para observabilidad

Los pipelines **deben propagar excepciones con `raise`**, no atraparlas y retornar normalmente. Un fallo silencioso significa exit code 0, lo que Cloud Run reporta como SUCCESS y rompe alertas y métricas.

Si el loader devuelve `None` (indicando fallo sin excepción), el pipeline lanza `RuntimeError` explícito.

### 5.7 Deploy manual con script local (no CI)

`scripts/deploy_desinc.ps1` ejecuta: pre-flight checks (rama `main`, sin cambios pendientes, project ID visible) → `gcloud builds submit` → `gcloud run jobs update`.

**No usamos GitHub Actions CI** porque el repo vive en cuenta personal de GitHub y conectar credenciales de GCP a esa cuenta tiene implicaciones de seguridad que requerirían autorización institucional. Decisión revisable si: (a) los pipelines tienen tests reales, (b) hay más de 3 pipelines en producción, o (c) TI autoriza un esquema seguro (org GitHub o WIF).

### 5.8 Convenciones de Git

- Carpetas en lowercase: `extract/`, `load/`, `transform/` (Linux case-sensitive, Windows no).
- Branches: `feat/`, `fix/`, `refactor/`, `chore/`, `docs/` + kebab-case en inglés.
- Mensajes de commit en imperativo, ≤50 chars en primera línea.
- Merge a `main` siempre vía PR con **squash and merge** (historia lineal).
- `.env`, `config/credentials/`, `env/`, `arbol.*`, `logs/` siempre gitignored.

### 5.9 Containerización con Chrome for Testing

`Dockerfile` instala Chrome y ChromeDriver de versión coincidente desde la API de Chrome for Testing (Google) en build time. Evita el problema histórico de versiones desincronizadas. En `_start_driver`:

- Local: window mode, `prefs` para directorio de descarga.
- Cloud Run: `--headless=new`, download path vía Chrome DevTools Protocol (`Page.setDownloadBehavior`). Headless ignora los `prefs` de descarga — usar CDP es obligatorio.

---

## 6. Topología de deployment

```
GitHub (main)
   │ git push
   ▼
deploy_desinc.ps1
   │ gcloud builds submit
   ▼
Cloud Build (cloudbuild.yaml)
   │ build + tag :sha + :latest
   ▼
Artifact Registry · us-central1
   │ gcloud run jobs update --image=...:latest
   ▼
Cloud Run Job · pipeline-desinc
   │
   ├── triggered by → Cloud Scheduler (0 6 * * * America/Mexico_City)
   ├── auth via    → Service Account (mbdataflow-runner)
   ├── secrets from → Secret Manager (SONDA_QUERY_USER, SONDA_QUERY_PASSWORD)
   ├── failure → Cloud Monitoring → email alert
   └── data out → Google Drive folder (SA tiene acceso Editor)
```

---

## 7. Observabilidad

- **Logs:** stdout/stderr del container → Cloud Logging automático.
- **Métricas:** Cloud Run estándar (`completed_execution_count`, etc.).
- **Alertas:**
  - `pipeline-desinc-failures`: dispara cuando hay alguna ejecución FAILED de `pipeline-desinc` (notifica por email).

Documentar nuevas alertas en `docs/monitoring.md`.

---

## 8. Costos actuales estimados

Operación de `pipeline_Desinc` en producción:

- **Cloud Run Job:** ejecución de ~3 min/día, 1 vCPU, 1 GiB — bajo free tier de Cloud Run.
- **Cloud Build:** ~3 min por deploy. Free tier 120 min/día — ampliamente cubierto.
- **Artifact Registry:** <1 GiB en imágenes. Free tier 0.5 GiB — pueden empezar a haber centavos si se acumulan tags viejos sin limpiar.
- **Cloud Scheduler:** 1 job free tier (hasta 3 gratis).
- **Cloud Monitoring/Logging:** dentro del free tier (50 GiB logs/mes).
- **Secret Manager:** 2 secretos activos, free tier 6.

**Total estimado:** <$1 USD/mes mientras solo opere `pipeline_Desinc`.

Cuando lleguemos a 3-4 pipelines en producción, conviene revisar acumulación de imágenes en Artifact Registry y configurar lifecycle policy.

---

## 9. Roadmap

### Overview
- El objetivo es implementar un pipeline que alimente el Dashboard de Intervalos y ciertos directorios/bases remotos.

- Para la implementación del pipeline de Intervalos `pipeline_Intervalos` (que emplea el reporte de Viaje), es necesario migrar rutas útiles de código de scripts en Colab. El proceso consta de diferentes métodos: Extract sobre la plataforma de Sonda :arrow_right: Load crudo a un directorio centralizado (sustituyendo ambos directorios de GO y de CC) :arrow_right: Transform del RV mediante los métodos migrados de Colab :arrow_right: Load hacia dos tablas distintas de BigQuery (INTERVALOS_Y_CUMPLIMIENTO, VIAJE).

Una posible redundancia del proceso yace en que implementamos dos métodos de Load del reporte de Viaje en "crudo". El primero hacia Drive, antes del transform y el segundo (hacia BQ) durante el Transform hacia la tabla de VIAJE. Temporalmente se toma la decisión de dejar comentada la actualización de datos en la tabla VIAJE.

### Inmediato
- Se completa el método Extract de reportes de Viaje. El scraper `Reporte_Viaje`. 
- Se implementa el proceso Load, hacia directorio centralizado MBDataFlow_ETL Drive.

### Próximo
- Se implementa el método Transform, que consiste en la migración, limpieza e implementación estructurada de código de notebooks en colab.

### A mediano plazo
- Se implementa el método Load a tablas 'INTERVALOS_Y_CUMPLIMIENTO' y 'VIAJE' de BigQuery. 
- Construcción del orquestador `pipeline_Intervalos`.

### Pospuesto
- CI con GitHub Actions o Cloud Build triggers — revisar cuando haya tests o autorización institucional.
- Tests automatizados de scrapers — requieren entorno con Chrome o mock muy elaborado, ROI bajo.
- Transform layer real — solo cuando un pipeline necesite transformación no trivial.

---

## 10. Glosario de archivos clave

| Archivo | Propósito |
|---|---|
| `config/settings.py` | Configuración centralizada. Lee env vars. Define `_runtime_path()`. |
| `utils/dates.py` | Helpers de fecha timezone-aware en CDMX. |
| `Dockerfile` | Imagen base con Python 3.13 + Chrome for Testing. |
| `cloudbuild.yaml` | Build config para Cloud Build. Tagging por SHA y `:latest`. |
| `scripts/deploy_desinc.ps1` | Deploy automatizado con pre-flight checks. |
| `.env.example` | Template documentando las env vars requeridas. |
| `requirements.txt` | Dependencias con versiones fijas. |

---

## 11. Cómo extender este documento

Cuando tomes una decisión arquitectónica nueva (estructura, dependencia, patrón), agrégala como subsección de §5 con: **qué decidiste, por qué, y cuándo reconsiderarla**. Este formato fuerza honestidad sobre los trade-offs.