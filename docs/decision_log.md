# Desicion Log - Pipeline Rerpote de Viajes

Registro cronológico de desiciones de proceso, testing y operación. Para decisiones arquitectónicas ver Architecture.md §5. Para el racional formal de un patrón, un ADR aparte.

## 16-07-2026 - Estrategia de smoke test del loader a BQ
Desición: probar el BigQuery loader contra tablas dummy antes de tocar producción, no directo contra Sonda.VIAJES.
Por qué: el loader hace DELETE-then-append; un mismmatch de schema o un bug en la guarda de rango corrompería una tabla histórica de la que dependen al menos 3 dashboards en LookerStudio/DataStudio.
Cómo: dataset `pruebas` separado, tablas `*_smoketest` creadas con CREATE TABLE LIKE para clonar el sistema exacto. Verifica: encaje de esquema, conteo, idempotencia (operador idempotente), guarda de rango.
Descartado: testing con cuenta personal, tengo control total de la SA, testing con credenciales de producción.

## 17-07-2026 - Eror en formato FECHA para carga a BigQuery
Decisión: añadir el formato '%Y-%m-%d %H:%M:%S' a la converisón to_datetime del campo FECHAS del CSV de viajes crudo.
Por qué: El CSV de la tabla VIAJE tiene un error de serialización en el formato DATETIME y la subida a BQ falla, ya que no indicamos horario (aunque sea trivial). Se justifica tocar este módulo congelado ya que el cambio altera la serialización al CSV posterior al transform, no el contenido en si. Se preserva la lógica del legacy.
Cómo: Endosando la reponsabilidad el método Transform: transform/transformers/Reporte_Viaje.py
Descartado: Endosar la responsabilidad en el método Load, sale de su función. 
 