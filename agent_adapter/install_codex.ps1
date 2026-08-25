$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$requirements = Join-Path $PSScriptRoot "requirements.txt"

if (-not (Test-Path -LiteralPath $venvPython)) {
    py -3 -m venv (Join-Path $PSScriptRoot ".venv")
}

& $venvPython -m pip install -r $requirements

codex mcp get videomatrix *> $null
if ($LASTEXITCODE -eq 0) {
    codex mcp remove videomatrix
}

codex mcp add videomatrix `
    --env "PYTHONPATH=$projectRoot" `
    -- $venvPython -m agent_adapter.mcp_server

Write-Host "VideoMatrix MCP is ready. Restart Codex to load it."
