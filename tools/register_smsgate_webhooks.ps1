param(
    [Parameter(Mandatory = $true)]
    [string]$Username,

    [Parameter(Mandatory = $true)]
    [AllowEmptyString()]
    [string]$Password,

    [Parameter(Mandatory = $true)]
    [string]$WebhookUrl,

    [ValidateSet("Cloud", "Private")]
    [string]$Mode = "Cloud",

    [string]$ApiBase = "",

    [string]$DeviceId = "",

    [string[]]$Events = @("sms:received", "sms:sent", "sms:delivered", "sms:failed")
)

if ($Mode -eq "Cloud") {
    $endpoint = "https://api.sms-gate.app/3rdparty/v1/webhooks"
}
else {
    if ([string]::IsNullOrWhiteSpace($ApiBase)) {
        throw "Para mode=Private debes indicar -ApiBase, por ejemplo https://tu-server.com/3rdparty/v1/webhooks"
    }
    $endpoint = $ApiBase.TrimEnd("/")
}

$basicAuthValue = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes("$Username`:$Password"))
$headers = @{
    "Authorization" = "Basic $basicAuthValue"
    "Content-Type"  = "application/json"
}

Write-Host "Endpoint de registro: $endpoint"
Write-Host "Webhook destino: $WebhookUrl"
Write-Host "Eventos: $($Events -join ', ')"
if (-not [string]::IsNullOrWhiteSpace($DeviceId)) {
    Write-Host "device_id: $DeviceId"
}

foreach ($eventName in $Events) {
    $body = @{
        url   = $WebhookUrl
        event = $eventName
    }

    if (-not [string]::IsNullOrWhiteSpace($DeviceId)) {
        $body["device_id"] = $DeviceId
    }

    $jsonBody = $body | ConvertTo-Json -Depth 5 -Compress

    try {
        $response = Invoke-RestMethod -Method Post -Uri $endpoint -Headers $headers -Body $jsonBody
        Write-Host "[OK] Registrado $eventName => $($response | ConvertTo-Json -Compress)"
    }
    catch {
        Write-Host ("[ERROR] Fallo al registrar {0}: {1}" -f $eventName, $_.Exception.Message) -ForegroundColor Red
    }
}
