<#
.SYNOPSIS
    Starts the GovStatScope backend and DynamoDB Local containers using Docker Compose.
.DESCRIPTION
    Ensures Docker Compose builds and starts the backend service along with its database dependency.
#>

$ErrorActionPreference = "Stop"

Write-Host "Starting DynamoDB Local and GovStatScope Backend..." -ForegroundColor Cyan
docker compose up --build backend dynamodb-local