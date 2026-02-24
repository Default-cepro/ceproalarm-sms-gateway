param(
    [string]$AdbSerial = "",
    [string]$Username = "sms",
    [Parameter(Mandatory = $true)]
    [string]$Password,
    [int]$DeviceApiPort = 8080,
    [int]$ForwardPort = 18080,
    [int]$ReversePort = 9876,
    [int]$ServerPort = 80,
    [string]$WebhookPath = "/webhook/sms/events",
    [string[]]$Events = @("sms:received", "sms:sent", "sms:delivered", "sms:failed"),
    [switch]$SkipEnvUpdate
)

function Build-AdbArgs {
    param([string[]]$CommandArgs)
    if ([string]::IsNullOrWhiteSpace($AdbSerial)) {
        return $CommandArgs
    }
    return @("-s", $AdbSerial) + $CommandArgs
}

function Run-Adb {
    param([string[]]$CommandArgs)
    & adb @(Build-AdbArgs -CommandArgs $CommandArgs)
    if ($LASTEXITCODE -ne 0) {
        throw "adb command failed: adb $($CommandArgs -join ' ')"
    }
}

function Set-Or-AppendEnvLine {
    param(
        [string[]]$Lines,
        [string]$Key,
        [string]$Value
    )
    $prefix = "$Key="
    $updated = $false
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i].StartsWith($prefix)) {
            $Lines[$i] = "$prefix$Value"
            $updated = $true
            break
        }
    }
    if (-not $updated) {
        $Lines += "$prefix$Value"
    }
    return ,$Lines
}

function Try-GetEndpoint {
    param(
        [string]$BaseUrl,
        [hashtable]$Headers
    )
    $candidates = @(
        "$BaseUrl/webhooks",
        "$BaseUrl/3rdparty/v1/webhooks"
    )

    foreach ($url in $candidates) {
        try {
            $resp = Invoke-WebRequest -Method Get -Uri $url -Headers $Headers -TimeoutSec 8
            return $url
        }
        catch {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $code = [int]$_.Exception.Response.StatusCode
                if ($code -in 200, 401, 403) {
                    return $url
                }
            }
            $m = $_.Exception.Message
            if ($m -match "401" -or $m -match "403") {
                return $url
            }
        }
    }
    return $null
}

Write-Host "1) Verificando ADB y dispositivo..."
Run-Adb -CommandArgs @("start-server")
Run-Adb -CommandArgs @("get-state")
Run-Adb -CommandArgs @("devices", "-l")

Write-Host "2) Configurando ADB forward (PC -> telefono API local)..."
Run-Adb -CommandArgs @("forward", "--remove-all")
Run-Adb -CommandArgs @("forward", "tcp:$ForwardPort", "tcp:$DeviceApiPort")

Write-Host "3) Configurando ADB reverse (telefono -> servidor local PC)..."
Run-Adb -CommandArgs @("reverse", "--remove-all")
Run-Adb -CommandArgs @("reverse", "tcp:$ReversePort", "tcp:$ServerPort")

$webhookUrl = "http://127.0.0.1:$ReversePort$WebhookPath"
$forwardBase = "http://127.0.0.1:$ForwardPort"

$basicAuthValue = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes("$Username`:$Password"))
$headers = @{
    "Authorization" = "Basic $basicAuthValue"
    "Content-Type"  = "application/json"
}

Write-Host "4) Detectando endpoint API del telefono por forward..."
$endpoint = Try-GetEndpoint -BaseUrl $forwardBase -Headers $headers
if (-not $endpoint) {
    # Fall back: standard path in local mode
    $endpoint = "$forwardBase/webhooks"
    Write-Host "No se pudo autodetectar endpoint, usando fallback: $endpoint" -ForegroundColor Yellow
}
Write-Host "Endpoint API detectado: $endpoint"

Write-Host "4.1) Precheck de disponibilidad local API..."
try {
    $healthCode = & curl.exe -s -o NUL -w "%{http_code}" -u "$Username`:$Password" "$forwardBase/"
    if ($healthCode -eq "200") {
        Write-Host "Local API reachable: 200"
    }
    else {
        Write-Host ("Precheck retorno HTTP {0}, se continua con registro para confirmar." -f $healthCode) -ForegroundColor Yellow
    }
}
catch {
    $detail = $_.Exception.Message
    Write-Host ("Precheck no concluyente en {0}: {1}" -f $forwardBase, $detail) -ForegroundColor Yellow
    Write-Host "Se continua con registro para validar conectividad real del endpoint /webhooks." -ForegroundColor Yellow
}

Write-Host "5) Registrando webhooks locales en telefono..."
foreach ($eventName in $Events) {
    $body = @{
        "url" = $webhookUrl
        "event" = $eventName
    } | ConvertTo-Json -Compress

    try {
        $response = Invoke-RestMethod -Method Post -Uri $endpoint -Headers $headers -Body $body -TimeoutSec 15
        Write-Host ("[OK] event={0} response={1}" -f $eventName, ($response | ConvertTo-Json -Compress))
    }
    catch {
        $msg = $_.Exception.Message
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $msg = "HTTP " + [int]$_.Exception.Response.StatusCode + " - " + $msg
        }
        Write-Host ("[ERROR] event={0} detail={1}" -f $eventName, $msg) -ForegroundColor Red
    }
}

Write-Host "6) Consultando lista actual de webhooks..."
try {
    $list = Invoke-RestMethod -Method Get -Uri $endpoint -Headers $headers -TimeoutSec 10
    Write-Host ($list | ConvertTo-Json -Depth 6)
}
catch {
    Write-Host ("No se pudo listar webhooks: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
}

if (-not $SkipEnvUpdate) {
    Write-Host "7) Actualizando .env para modo local API..."
    $envPath = ".env"
    $lines = @()
    if (Test-Path $envPath) {
        $lines = Get-Content $envPath
    }
    $lines = Set-Or-AppendEnvLine -Lines $lines -Key "SMS_GATE_LOCAL_API_ENABLED" -Value "1"
    $lines = Set-Or-AppendEnvLine -Lines $lines -Key "SMS_GATE_LOCAL_API_BASE_URL" -Value "http://127.0.0.1:$ForwardPort"
    $lines = Set-Or-AppendEnvLine -Lines $lines -Key "SMS_GATE_LOCAL_API_USERNAME" -Value $Username
    $lines = Set-Or-AppendEnvLine -Lines $lines -Key "SMS_GATE_LOCAL_API_PASSWORD" -Value $Password
    Set-Content -Path $envPath -Value $lines -Encoding UTF8
    Write-Host "Archivo .env actualizado para este dispositivo."
}

Write-Host ""
Write-Host "Configuracion completada."
Write-Host "Webhook URL usada por el telefono: $webhookUrl"
Write-Host "Asegurate de tener tu FastAPI activo en localhost:$ServerPort."
