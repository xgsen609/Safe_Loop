[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$originalLocation = Get-Location
$backendProcess = $null

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Import-SupabaseEnvironment {
    $statusLines = & supabase status -o env
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the local Supabase environment."
    }
    foreach ($line in $statusLines) {
        if ($line -match '^([A-Z_]+)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim().Trim('"')
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

try {
    Set-Location $repoRoot
    Require-Command "python"
    Require-Command "npm"
    Require-Command "supabase"

    if (-not (Test-Path "backend\.venv\Scripts\python.exe")) {
        python -m venv backend\.venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment." }
    }
    $python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
    & $python -m pip install -r backend\requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
    npm --prefix frontend ci --prefer-offline --no-audit
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }

    supabase start
    if ($LASTEXITCODE -ne 0) { throw "Supabase did not start." }
    supabase db reset --local
    if ($LASTEXITCODE -ne 0) { throw "Supabase migrations did not apply." }
    Import-SupabaseEnvironment

    $env:DATABASE_URL = $env:DB_URL
    $env:SUPABASE_URL = $env:API_URL
    $env:SUPABASE_SERVICE_ROLE_KEY = $env:SERVICE_ROLE_KEY
    $env:SUPABASE_JWT_SECRET = if (Test-Path Env:JWT_SECRET) { $env:JWT_SECRET } else { "" }
    $env:NEXT_PUBLIC_SUPABASE_URL = $env:API_URL
    $env:NEXT_PUBLIC_SUPABASE_ANON_KEY = $env:ANON_KEY
    $env:APP_ENV = "local"
    $env:ALLOW_DEBUG_AUTH = "false"
    $env:AI_PROVIDER = "stub"
    $env:BACKEND_URL = "http://127.0.0.1:8000"
    $env:NEXT_PUBLIC_BACKEND_URL = "http://127.0.0.1:8000"
    $env:SITE_TIMEZONE = "Asia/Singapore"

    & $python scripts\apply_demo_seed.py
    if ($LASTEXITCODE -ne 0) { throw "Demo data did not load." }
    Push-Location backend
    try {
        & .\.venv\Scripts\python.exe -m app.rag.backfill
        if ($LASTEXITCODE -ne 0) { throw "RAG embedding backfill failed." }
    } finally {
        Pop-Location
    }

    $backendStart = @{
        FilePath = $python
        ArgumentList = @(
            "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
            "--host", "127.0.0.1", "--port", "8000"
        )
        WorkingDirectory = $repoRoot
        WindowStyle = "Hidden"
        PassThru = $true
    }
    $backendProcess = Start-Process @backendStart

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) { throw "Backend health check did not become ready." }

    Write-Host "SafeLoop demo: http://127.0.0.1:3000/en"
    Write-Host "Demo password for every listed account: SafeLoopDemo!2026"
    npm --prefix frontend run dev
} finally {
    if ($null -ne $backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id
    }
    Set-Location $originalLocation
}
