param(
    [int]$Port = 8000
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:REPOPILOT_REPO_PATH = $projectRoot
$env:PYTHONPATH = (Join-Path $projectRoot "src")

foreach ($name in "REPOPILOT_API_KEY", "REPOPILOT_MODEL_BASE_URL", "REPOPILOT_MODEL_NAME") {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, "Process"))) {
        $saved = [Environment]::GetEnvironmentVariable($name, "User")
        if (-not [string]::IsNullOrWhiteSpace($saved)) {
            [Environment]::SetEnvironmentVariable($name, $saved, "Process")
        }
    }
}

Write-Host "Starting RepoPilot at http://127.0.0.1:$Port"
python -m uvicorn repopilot.app:app --app-dir src --host 127.0.0.1 --port $Port
