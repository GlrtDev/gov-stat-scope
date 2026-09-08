<#
.SYNOPSIS
    Starts the GovStatScope backend and DynamoDB Local containers using Docker Compose.
.DESCRIPTION
    Ensures Docker Compose builds and starts the backend service along with its database dependency.
    Supports local step-debugging via debugpy on port 5678.
#>
param (
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

# Ensure script runs from the project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$ScriptDir\.."

Write-Host "==> Starting DynamoDB Local dependency..." -ForegroundColor Yellow
docker compose up -d dynamodb-local

Write-Host "==> Building backend container..." -ForegroundColor Cyan
docker compose build backend

if ($Debug) {
    Write-Host "==> Debug mode enabled. Exposing port 5678 and waiting for VS Code client..." -ForegroundColor Magenta
    
    $DebugArgs = @(
        "run",
        "-it",
        "--rm",
        "-p", "5678:5678",
        "-p", "8000:8000",
        "backend",
        "python", "-m", "debugpy", "--listen", "0.0.0.0:5678", "--wait-for-client", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"
    )
    
    docker compose @DebugArgs
} else {
    Write-Host "==> Starting backend in standard mode..." -ForegroundColor Green
    docker compose up backend
}