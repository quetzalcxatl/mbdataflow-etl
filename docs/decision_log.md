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
 
 ## 21-07-2026 - Eliminación de carga a Drive (pipeline Intervalos)
Decisión: Se decide eliminar la rama que carga los CSV crudos de Sonda al directorio centralizado (End) de pipelines en Drive.
Por qué: En conjunto es una decisión de PM. Dado que ya existe una tabla histórico en BigQuery.
Cómo: Dejando fuera del orquestador del pipeline el paso específico que invoca Viaje_drive_loader.py
Descartado: Incluirlo por debajo de las indicaciones del PM. La desición debe ser respetada, pese a quedarnos sin un backup.

## 21-07-2026 - Condicionamiento de carga a Drive como un backup opcional (pipeline Intervalos)
Desición: Se decide implementar el método de carga a drive dentro de un condicional `DRIVE_BACKUP=True,False`, declarando la naturaleza de método secundario no bloqueador del pipeline Intervalos. De ser activado, un fallo de carag a Drive se logea como warning, el pipeline debe continuar.
Por qué: Me parce una decisión más prudente que eliminar de tajo el método en el orquestador. No va directamente en contra de las decisiones de PM.
Cómo: Introduciendo el paso que invoca Viaje_drive_loader.py bajo un bloque condicional de `DRIVE_BACKUP`.
Descartado: Eliminar totalmente la opción de implementar el método de carga a Drive en el orquestador de Viajes.

## 10-08-2026 - Auditoría de datos generados por pipeline_Viaje contra legacy
Desición: Se decide tomar por concluida/exitosa la auditoría de datos que genera el pipeline contra los datos de los notebook legacy.
Por qué: Tras un análisis multi-factor (ventanas temporales, duplicación, offset de rango, frontera de día, divergencia de parseo y bug de la rama INTERVALOS_Y_CUMPLIMIENTOS), se aisla la discrepancia entre tablas como consecuencia de in filtro meramente estocástico, que genera resultados siempre distintos.
Cómo: El método `transform/transformers/Reporte_Viaje.py`, tanto del pipeline como de los notebook legacy poseen un filtro IsolationForest, el cual es naturalmente estocástico, si no se determina una semilla de producción y un parámetro de contaminación fijos. Podemos esperar un conjunto de datos divergentes, sin embargo el comportamiento de los datos filtrados es el mismo.  