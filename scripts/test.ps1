param (
    [ValidateSet("unit", "integration", "all")]
    [string]$TestType = "unit"
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
        docker compose run --rm test-runner tests/unit -v
    }
    "integration" {
        Write-Host "==> Booting DynamoDB local dependency..." -ForegroundColor Yellow
        docker compose up -d dynamodb-local
        try {
            Write-Host "==> Executing integration tests..." -ForegroundColor Green
            docker compose run --rm test-runner tests/integration -v
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
            docker compose run --rm test-runner tests -v
        }
        finally {
            Write-Host "==> Cleaning up test containers..." -ForegroundColor Yellow
            docker compose stop dynamodb-local
        }
    }
}