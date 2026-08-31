param (
    [ValidateSet("unit", "integration", "all", "single")]
    [string]$TestType = "unit",
    [string]$TestPath = "",
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$ScriptDir\.."

Write-Host "==> Building backend test container..." -ForegroundColor Cyan
docker compose build test-runner

$RunArgs = @("--rm")
if ($Debug) {
    Write-Host "==> Debug mode enabled. Exposing port 5678 and waiting for VS Code client..." -ForegroundColor Magenta
    $RunArgs += "-p"
    $RunArgs += "5678:5678"
    # Added --subprocesses to catch breakpoints in async tasks/sub-threads
    $TestCommand = "python -m debugpy --listen 0.0.0.0:5678 --wait-for-client --subprocesses -m pytest $CleanPath -v --asyncio-mode=auto"
} else {
    $TestCommand = "pytest $CleanPath -v --asyncio-mode=auto"
}

switch ($TestType) {
    "unit" {
        Write-Host "==> Executing unit tests..." -ForegroundColor Green
        if ($Debug) {
            docker compose run @RunArgs test-runner python -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m pytest tests/unit -v --asyncio-mode=auto
        } else {
            docker compose run @RunArgs test-runner pytest tests/unit -v --asyncio-mode=auto
        }
    }
    "integration" {
        Write-Host "==> Booting DynamoDB local dependency..." -ForegroundColor Yellow
        docker compose up -d dynamodb-local
        try {
            Write-Host "==> Executing integration tests..." -ForegroundColor Green
            if ($Debug) {
                docker compose run @RunArgs test-runner python -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m pytest tests/integration -v --asyncio-mode=auto
            } else {
                docker compose run @RunArgs test-runner pytest tests/integration -v --asyncio-mode=auto
            }
        }
        finally {
            Write-Host "==> Cleaning up test containers..." -ForegroundColor Yellow
            docker compose stop dynamodb-local
        }
    }
    "all" {
        Write-Host "==> Booting DynamoDB local dependency..." -ForegroundColor Yellow
        docker compose up -d dynamodb-local
        try {
            Write-Host "==> Executing full test suite..." -ForegroundColor Green
            if ($Debug) {
                docker compose run @RunArgs test-runner python -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m pytest tests -v --asyncio-mode=auto
            } else {
                docker compose run @RunArgs test-runner pytest tests -v --asyncio-mode=auto
            }
        }
        finally {
            Write-Host "==> Cleaning up test containers..." -ForegroundColor Yellow
            docker compose stop dynamodb-local
        }
    }
    "single" {
        if ([string]::IsNullOrWhiteSpace($TestPath)) {
            Write-Error "TestPath must be provided when TestType is 'single'."
        }
        Write-Host "==> Booting DynamoDB local dependency..." -ForegroundColor Yellow
        docker compose up -d dynamodb-local
        try {
            Write-Host "==> Executing single test: $TestPath..." -ForegroundColor Green
            $CleanPath = $TestPath -replace '\\', '/'
            if ($Debug) {
                docker compose run @RunArgs test-runner python -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m pytest $CleanPath -v --asyncio-mode=auto
            } else {
                docker compose run @RunArgs test-runner pytest $CleanPath -v --asyncio-mode=auto
            }
        }
        finally {
            Write-Host "==> Cleaning up test containers..." -ForegroundColor Yellow
            docker compose stop dynamodb-local
        }
    }
}