# Architecture

Estado del proyecto **MBDataFlow_ETL** y decisiones de diseño tomadas hasta la fecha. Documento vivo: actualizar cuando una decisión nueva afecte la estructura del sistema.

---

## 1. Propósito

Monorepo de pipelines ETL/EL para datos operativos de Metrobús CDMX. Cada pipeline extrae datos de una fuente operativa (plataforma Sonda Sinóptico Plus, archivos en Drive, etc.), opcionalmente los transforma, y los carga a Google Drive y/o BigQuery. Despliegue como Cloud Run Jobs en GCP, programados con Cloud Scheduler.

---

## 2. Estado de pipelines

| Pipeline | Tipo | Estado | Trigger |
|---|---|---|---|
| `pipeline_Desinc` | EL | Producción | Cloud Scheduler diario · 5:00 AM CDMX |
| `pipeline_Circuitos` | EL | Producción | Cloud Scheduler semanal · 7:00 AM CDMX|
| `pipeline_CanBus` | EL | Pausado · calidad de datos upstream | — |
| `pipeline_rangofechas_canbus` | EL | Pausado · calidad de datos upstream | — |
| `pipeline_Pasos` | EL | Producción | Cloud Scheduler semanal · 6:00 AM CDMX |
| `pipeline_Viaje` | ETL-T | Local | - | 

---

## 3. Stack

- **Lenguaje:** Python 3.13 y GoogleSQL
- **Cloud:** GCP — Cloud Run Jobs, Cloud Build, Cloud Scheduler, Cloud Monitoring, Artifact Registry, Secret Manager
- **Storage:** Google Drive (vía `google-api-python-client`), BigQuery (vía )
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
│   ├── credentials/...
│   ├── __init__.py
│   ├── settings.py
│   └── sonda_pv_config.json
├── docs/
│   ├── decision_log.md
│   └── monitoring.md
├── env/...
│   ├── Scripts/...
│   ├── .gitignore
│   └── pyvenv.cfg
├── extract/
│   ├── helpers/
│   │   ├── download_helper.py
│   │   └── intervalo_fecha_helper.py
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── CanBus.py
│   │   ├── Circuitos.py
│   │   ├── Desincorporaciones.py
│   │   ├── FlotaVehicular.py
│   │   ├── Pasos.py
│   │   ├── recover_sonda_pv.py
│   │   ├── Reporte_Viaje.py
│   │   └── Reportes_Operador.py
│   ├── __init__.py
│   └── base.py
├── load/
│   ├── loaders/
│   │   ├── BigQuery_loader.py
│   │   ├── CAN_drive_loader.py
│   │   ├── Circuitos_drive_loader.py
│   │   ├── debug_drive_loader.py
│   │   ├── Desincorporaciones_drive_loader.py
│   │   ├── google_drive_loader.py
│   │   ├── Pasos_drive_loader.py
│   │   ├── Reportes_Operador_drive_loader.py
│   │   └── Viaje_drive_loader.py
│   ├── schemas/
│   │   └── viaje.py
│   ├── __init__.py
│   └── base.py
├── logs/
├── pipelines/
│   ├── __init__.py
│   ├── pipeline_CanBus.py
│   ├── pipeline_Circuitos.py
│   ├── pipeline_Desinc.py
│   ├── pipeline_Pasos.py
│   ├── pipeline_rangofechas_canbus.py
│   └── pipeline_Viaje.py
├── scripts/
│   ├── audit_viaje_parity.py
│   ├── deploy_circuitos.ps1
│   ├── deploy_desinc.ps1
│   ├── deploy_job_circuitos.ps1
│   ├── deploy_job_desinc.ps1
│   ├── deploy_job_pasos.ps1
│   ├── deploy_job_viaje.ps1
│   ├── deploy_pasos.ps1
│   ├── deploy_viaje.ps1
│   ├── setup_scheduler_circuitos.ps1
│   ├── setup_scheduler_desinc.ps1
│   ├── setup_scheduler_pasos.ps1
│   ├── setup_scheduler_viaje.ps1
│   ├── smoke_test_bigquery_loader.py
│   └── smoketest_bq_sql_runner.py
├── tests/
│   ├── test_extract/
│   ├── test_load/
│   └── test_transform/
├── transform/
│   ├── sql/
│   │   └── intervalos_dinamicos.sql
│   ├── transformers/
│   │   ├── __init__.py
│   │   ├── CanBus.py
│   │   ├── FlotaVehicular.py
│   │   └── Reporte_Viaje.py
│   ├── __init__.py
│   ├── base.py
│   └── bq_sql_runner.py
├── utils/
│   ├── __init__.py
│   ├── dates.py
│   ├── logger.py
│   └── turno.py
├── .dockerignore
├── .env.example
├── .gcloudignore
├── .gitignore
├── Architecture.md
├── cloudbuild.yaml
├── Dockerfile
├── LICENSE
├── README.MD
└── requirements.txt
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


### 5.10 Exclusión mutua entre pipelines contra Sonda

La Central de Descargas de Sonda es **por cuenta, no por reporte**. Todos los
scrapers que usan `SONDA_QUERY_USER` comparten la misma cola de solicitudes.

Los scrapers identifican su propio archivo como "la fila más reciente de la
tabla" (ver `Pasos_Scraper._download_latest`, `Viaje_Scraper._download`). Esa
premisa se rompe si dos pipelines encolan solicitudes de forma solapada: el
segundo puede descargar el archivo del primero **sin lanzar excepción** — el
Job termina en SUCCESS con datos equivocados en Drive.

**Decisión:** la separación es TEMPORAL, garantizada por Cloud Scheduler. No
hay lock ni coordinación en código.

| Job | Horario CDMX | Duración observada |
|---|---|---|
| `pipeline-desinc` | diario 05:00 | ~3 min |
| `pipeline-pasos`  | lunes 06:00  | ~6 min |
| `pipeline-circ`   | 07:00, día por confirmar | ~2 min |

**Antes de agregar o mover cualquier Job que toque Sonda**, revisar
`gcloud scheduler jobs list --location=us-central1` y verificar que la ventana
[inicio, inicio + duración worst-case] no solape con ninguna otra.

**Cuándo reconsiderar:** cuando haya 4+ pipelines contra Sonda, o cuando la
duración de alguno se vuelva impredecible. La separación temporal deja de
escalar y hará falta un lock real (Firestore, GCS object lock) o una segunda
cuenta Sonda.

---

## 6. Topología de deployment

```
GitHub (main)
   │ git push
   ▼
deploy_desinc.ps1
   │ gcloud builds s2ubmit
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

## 11. Cómo se extiende/actualiza este documento

Cuando se tome una decisión arquitectónica nueva (estructura, dependencia, patrón), se agrega como subsección de §5 con: **qué se decide, por qué, y cuándo reconsiderar**. De esta manera aseguramos tener honestidad sobre los **trade-offs** o **decisiones**.