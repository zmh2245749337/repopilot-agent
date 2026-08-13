$secureKey = Read-Host "Enter Zhipu API Key" -AsSecureString
$plainKey = [System.Net.NetworkCredential]::new("", $secureKey).Password

if ([string]::IsNullOrWhiteSpace($plainKey)) {
    throw "API Key cannot be empty."
}

[Environment]::SetEnvironmentVariable("REPOPILOT_API_KEY", $plainKey, "User")
[Environment]::SetEnvironmentVariable(
    "REPOPILOT_MODEL_BASE_URL",
    "https://open.bigmodel.cn/api/paas/v4",
    "User"
)
[Environment]::SetEnvironmentVariable("REPOPILOT_MODEL_NAME", "glm-4.7-flash", "User")

$env:REPOPILOT_API_KEY = $plainKey
$env:REPOPILOT_MODEL_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:REPOPILOT_MODEL_NAME = "glm-4.7-flash"

Remove-Variable plainKey, secureKey

$saved = [bool][Environment]::GetEnvironmentVariable("REPOPILOT_API_KEY", "User")
Write-Host "Zhipu configuration saved: $saved"
Write-Host "Model: glm-4.7-flash"
Write-Host "The current shell and future terminals are configured."
