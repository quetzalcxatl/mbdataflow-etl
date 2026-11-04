<#
.SYNOPSIS
  Descarga y resume los logs de un Cloud Run Job en un rango de fechas.

.DESCRIPTION
  Consulta Cloud Logging (gcloud logging read) para resource.type=cloud_run_job
  filtrando por job_name y rango de timestamp. Guarda el JSON crudo y un CSV
  aplanado, e imprime un resumen por ejecucion (errores y ultimo mensaje de error).
  Las fechas se interpretan en hora LOCAL de tu equipo y se convierten a UTC.

.PARAMETER StartDate   Inicio del rango (inclusive). Ej: 2026-10-27
.PARAMETER EndDate     Fin del rango. Si es solo fecha (00:00), se incluye el dia completo.
.PARAMETER Days        Si no das StartDate: ultimos N dias calendario, incluyendo hoy. Default 7.
.PARAMETER MinSeverity Severidad minima (DEFAULT = todo). Usa ERROR para ir directo a las fallas.
.PARAMETER Region      Necesaria solo con -ShowExecutions (ej. us-central1).
.PARAMETER ShowExecutions  Lista tambien las ejecuciones del job (exito/fallo) en el rango.
.PARAMETER DryRun      Imprime el comando gcloud y el filtro sin ejecutarlo.

.EXAMPLE
  .\Get-PipelineLogs.ps1 -Days 5 -MinSeverity ERROR
.EXAMPLE
  .\Get-PipelineLogs.ps1 -StartDate 2026-10-27 -EndDate 2026-11-02 -Region us-central1 -ShowExecutions
#>
[CmdletBinding()]
param(
    [string]$Project = 'centrodecontrol',
    [string]$Job = 'pipeline-pv',
    [string]$Region,
    [string]$Execution,
    [switch]$Summary,
    [datetime]$StartDate,
    [datetime]$EndDate,
    [int]$Days = 7,
    [ValidateSet('DEFAULT', 'DEBUG', 'INFO', 'NOTICE', 'WARNING', 'ERROR', 'CRITICAL')]
    [string]$MinSeverity = 'DEFAULT',
    [int]$Limit = 5000,
    [string]$OutDir = '.\scripts\logs_pipelines',
    [switch]$ShowExecutions,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function ConvertTo-LocalTs($v) {
    if ($v -is [datetime]) { return $v.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss') }
    # la API devuelve hasta 9 decimales; .NET tolera 7 -> recorto a 6
    $s = ([string]$v) -replace '(\.\d{1,6})\d*Z$', '$1Z'
    return ([DateTimeOffset]::Parse($s, [Globalization.CultureInfo]::InvariantCulture)).ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss')
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "No encuentro 'gcloud' en el PATH. Abre una terminal donde Google Cloud SDK este disponible."
}

# --- Rango de fechas (hora local -> UTC) ---
if (-not $PSBoundParameters.ContainsKey('EndDate')) {
    $EndDate = Get-Date
}
elseif ($EndDate.TimeOfDay.TotalSeconds -eq 0) {
    $EndDate = $EndDate.AddDays(1)   # fecha sola => incluir el dia completo
}
if (-not $PSBoundParameters.ContainsKey('StartDate')) {
    $StartDate = (Get-Date).Date.AddDays( - ($Days - 1))
}
if ($StartDate -ge $EndDate) { throw "StartDate ($StartDate) debe ser anterior a EndDate ($EndDate)." }

$fmt = "yyyy-MM-dd'T'HH:mm:ss'Z'"
$startUtc = $StartDate.ToUniversalTime().ToString($fmt)
$endUtc = $EndDate.ToUniversalTime().ToString($fmt)

# --- Filtro de Cloud Logging ---
$filter = 'resource.type="cloud_run_job" AND resource.labels.job_name="{0}" AND timestamp>="{1}" AND timestamp<"{2}"' -f $Job, $startUtc, $endUtc
if ($MinSeverity -ne 'DEFAULT') { $filter += " AND severity>=$MinSeverity" }
if ($Execution) { $filter += ' AND labels."run.googleapis.com/execution_name"="{0}"' -f $Execution }

Write-Host "Job      : $Job  (proyecto $Project)"
Write-Host "Rango    : $($StartDate.ToString('yyyy-MM-dd HH:mm')) -> $($EndDate.ToString('yyyy-MM-dd HH:mm')) hora local"
Write-Host "Filtro   : $filter`n"

if ($DryRun) {
    Write-Host "POST https://logging.googleapis.com/v2/entries:list  (proyecto $Project, limit $Limit)"
    return
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp = '{0:yyyyMMdd}_{1:yyyyMMdd}' -f $StartDate, $EndDate.AddSeconds(-1)
# --- Consulta via REST (evita el quoting de gcloud.cmd) ---
$token = ((& gcloud auth print-access-token 2>$null) -join '').Trim()
if ($LASTEXITCODE -ne 0 -or -not $token) {
    throw "No pude obtener token. Ejecuta: gcloud auth login"
}
$headers = @{ Authorization = "Bearer $token" }
$uri = 'https://logging.googleapis.com/v2/entries:list'
$entries = New-Object System.Collections.Generic.List[object]
$pageToken = $null
do {
    $body = @{
        resourceNames = @("projects/$Project")
        filter        = $filter
        orderBy       = 'timestamp asc'
        pageSize      = [Math]::Min(1000, $Limit)
    }
    if ($pageToken) { $body.pageToken = $pageToken }
    try {
        $resp = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers `
            -ContentType 'application/json' -Body ($body | ConvertTo-Json -Depth 5)
    }
    catch {
        throw "Logging API fallo: $($_.Exception.Message) $($_.ErrorDetails.Message)"
    }
    if ($resp.entries) { $entries.AddRange([object[]]@($resp.entries)) }
    $pageToken = $resp.nextPageToken
} while ($pageToken -and $entries.Count -lt $Limit)

$jsonPath = Join-Path $OutDir "logs_${Job}_$stamp.json"
ConvertTo-Json -InputObject $entries.ToArray() -Depth 20 | Set-Content -Path $jsonPath -Encoding UTF8
if ($entries.Count -eq 0) {
    Write-Warning "Sin entradas. Revisa: nombre del job, proyecto, rango, o que el job sea realmente Cloud Run (no Dataflow/Cloud Build/Workflows)."
    return
}
if ($entries.Count -ge $Limit) {
    Write-Warning "Se alcanzo -Limit ($Limit): puede haber mas logs. Sube -Limit o acota el rango / usa -MinSeverity ERROR."
}

# --- Aplanar ---
$rows = foreach ($e in $entries) {
    $msg = ''
    if ($e.textPayload) { $msg = $e.textPayload }
    elseif ($e.jsonPayload.message) { $msg = $e.jsonPayload.message }
    elseif ($e.jsonPayload) { $msg = ($e.jsonPayload | ConvertTo-Json -Compress -Depth 5) }
    elseif ($e.protoPayload.status.message) { $msg = $e.protoPayload.status.message }
    if ($e.jsonPayload -and $e.jsonPayload.message) {
        foreach ($k in 'stack_trace', 'stackTrace', 'exception', 'traceback', 'error') {
            if ($e.jsonPayload.$k) { $msg += "`n  [$k] " + ($e.jsonPayload.$k | Out-String).TrimEnd() }
        }
    }

    [pscustomobject]@{
        Timestamp = ConvertTo-LocalTs $e.timestamp
        Severity  = $e.severity
        Execution = $e.labels.'run.googleapis.com/execution_name'
        Message   = $msg
    }
}
$csvPath = Join-Path $OutDir "logs_${Job}_$stamp.csv"
$rows | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8

# --- Logs completos (consola + archivo .log) ---
Write-Host "Entradas: $($rows.Count)   JSON: $jsonPath   CSV: $csvPath"
$logPath = Join-Path $OutDir "logs_${Job}_$stamp.log"
$out = New-Object System.Collections.Generic.List[string]
$prevExec = '__none__'
foreach ($r in $rows) {
    if ($r.Execution -ne $prevExec) {
        $hdr = "`n===== Execution: {0} =====" -f $(if ($r.Execution) { $r.Execution } else { '(sin execution)' })
        Write-Host $hdr -ForegroundColor Cyan
        $out.Add($hdr)
        $prevExec = $r.Execution
    }
    $line = '[{0}] {1,-8} {2}' -f $r.Timestamp, $r.Severity, $r.Message
    $color = switch -Regex ($r.Severity) {
        'ERROR|CRITICAL|ALERT|EMERGENCY' { 'Red' }
        'WARNING' { 'Yellow' }
        default { 'Gray' }
    }
    Write-Host $line -ForegroundColor $color
    $out.Add($line)
}
Set-Content -Path $logPath -Value $out -Encoding UTF8
Write-Host "`nLog completo guardado en: $logPath`n"

# --- Resumen por ejecucion (opcional: -Summary) ---
if ($Summary) {
Write-Host "=== Resumen por ejecucion ===" -ForegroundColor Cyan
$rows | Group-Object Execution | Sort-Object { ($_.Group | Select-Object -First 1).Timestamp } | ForEach-Object {
    $errs = @($_.Group | Where-Object { $_.Severity -in 'ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY' })
    $last = $errs | Select-Object -Last 1
    $lastMsg = if ($last) { ($last.Message -replace '\s+', ' ') } else { '' }
    if ($lastMsg.Length -gt 200) { $lastMsg = $lastMsg.Substring(0, 200) + '...' }
    [pscustomobject]@{
        Execution = if ($_.Name) { $_.Name } else { '(sin execution)' }
        Inicio    = ($_.Group | Select-Object -First 1).Timestamp
        Logs      = $_.Count
        Errores   = $errs.Count
        UltimoError = $lastMsg
    }
} | Format-Table -AutoSize -Wrap
}

# --- Ejecuciones del job (opcional) ---
if ($ShowExecutions) {
    if (-not $Region) { throw "-ShowExecutions requiere -Region (ej. us-central1)." }
    $exArgs = @('run', 'jobs', 'executions', 'list', "--job=$Job", "--region=$Region", "--project=$Project", '--limit=200', '--format=json')
    $exRaw = (& gcloud @exArgs 2>$null) -join "`n"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($exRaw)) {
        Write-Warning "No pude listar ejecuciones (region/permisos?)."
    }
    else {
        Write-Host "=== Ejecuciones del job en el rango ===" -ForegroundColor Cyan
        $exRaw | ConvertFrom-Json | ForEach-Object {
            $created = ([datetime]$_.metadata.creationTimestamp)
            if ($created -ge $StartDate.ToUniversalTime() -and $created -lt $EndDate.ToUniversalTime()) {
                [pscustomobject]@{
                    Execution  = $_.metadata.name
                    Creada     = $created.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss')
                    Exitosas   = [int]$_.status.succeededCount
                    Fallidas   = [int]$_.status.failedCount
                    Condicion  = ($_.status.conditions | Where-Object type -eq 'Completed' | Select-Object -First 1).message
                }
            }
        } | Sort-Object Creada | Format-Table -AutoSize -Wrap
    }
}