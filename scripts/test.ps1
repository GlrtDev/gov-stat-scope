param (
    [ValidateSet("unit", "integration", "all", "single")]
    [string]$TestType = "unit",
    [string]$TestPath = ""
)

$ErrorActionPreference = "Stop"

# Navigate to repo root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$ScriptDir\.."

Write-Host "==> Building backend test container..." -ForegroundColor Cyan
docker compose build test-runner

switch ($TestType) {
    "unit" {
        Write-Host "==> Executing unit tests..." -ForegroundColor Green
        docker compose run --rm test-runner pytest tests/unit -v --asyncio-mode=auto
    }
    "integration" {
        Write-Host "==> Booting DynamoDB local dependency..." -ForegroundColor Yellow
        docker compose up -d dynamodb-local
        try {
            Write-Host "==> Executing integration tests..." -ForegroundColor Green
            docker compose run --rm test-runner pytest tests/integration -v --asyncio-mode=auto
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
            docker compose run --rm test-runner pytest tests -v --asyncio-mode=auto
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
            docker compose run --rm test-runner pytest $CleanPath -v --asyncio-mode=auto
        }
        finally {
            Write-Host "==> Cleaning up test containers..." -ForegroundColor Yellow
            docker compose stop dynamodb-local
        }
    }
}