# Desicion Log - Pipeline Rerpote de Viajes

Registro cronológico de desiciones de proceso, testing y operación. Para decisiones arquitectónicas ver Architecture.md §5. Para el racional formal de un patrón, un ADR aparte.

## 16-07-2026 - Estrategia de smoke test del loader a BQ
Desición: probar el BigQuery loader contra tablas dummy antes de tocar producción, no directo contra Sonda.VIAJES.
Por qué: el loader hace DELETE-then-append; un mismmatch de schema o un bug en la guarda de rango corrompería una tabla histórica de la que dependen al menos 3 dashboards en LookerStudio/DataStudio.
Cómo: dataset `pruebas` separado, tablas `*_smoketest` creadas con CREATE TABLE LIKE para clonar el sistema exacto. Verifica: encaje de esquema, conteo, idempotencia (operador idempotente), guarda de rango.
Descartado: testing con cuenta personal, tengo control total de la SA, testing con credenciales de producción.
