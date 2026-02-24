param(
    [string]$Url = "http://127.0.0.1:80/webhook/sms/events",
    [Parameter(Mandatory = $true)]
    [string]$SigningKey,
    [string]$PhoneNumber = "4243616194",
    [string]$Message = "STATUS OK"
)

$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
$eventId = "evt-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$messageId = "msg-" + [guid]::NewGuid().ToString("N").Substring(0, 12)

$payload = @{
    deviceId  = "manual-test-device"
    event     = "sms:received"
    id        = $eventId
    payload   = @{
        messageId  = $messageId
        message    = $Message
        phoneNumber = $PhoneNumber
        simNumber  = 1
        receivedAt = [DateTimeOffset]::Now.ToString("o")
    }
    webhookId = "manual-test-webhook"
}

$rawBody = $payload | ConvertTo-Json -Depth 8 -Compress
$hmac = [System.Security.Cryptography.HMACSHA256]::new([System.Text.Encoding]::UTF8.GetBytes($SigningKey))
$hash = $hmac.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($rawBody + $timestamp))
$signature = -join ($hash | ForEach-Object { $_.ToString("x2") })

$headers = @{
    "Content-Type" = "application/json"
    "X-Timestamp"  = $timestamp
    "X-Signature"  = $signature
}

Write-Host "POST $Url"
Write-Host "event id: $eventId"
Write-Host "message id: $messageId"

try {
    $response = Invoke-RestMethod -Method Post -Uri $Url -Headers $headers -Body $rawBody
    Write-Host "Respuesta OK:"
    $response | ConvertTo-Json -Depth 8
}
catch {
    Write-Host "Error enviando evento firmado: $($_.Exception.Message)" -ForegroundColor Red
    throw
}
