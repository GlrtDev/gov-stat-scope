# scripts/deploy.ps1
$ErrorActionPreference = 'Stop'

$configPath = Join-Path $PSScriptRoot deploy_frontend.config.json

if (-not (Test-Path $configPath)) {
    Write-Error "Deploy config not found: $configPath. Copy deploy_frontend.config.example.json and fill in your values."
}

$config = Get-Content $configPath -Raw | ConvertFrom-Json

$bucketName     = $config.bucketName
$distributionId = $config.distributionId
$awsProfile     = $config.awsProfile

if ([string]::IsNullOrWhiteSpace($bucketName))     { Write-Error 'deploy_frontend.config.json: "bucketName" must not be empty.' }
if ([string]::IsNullOrWhiteSpace($distributionId)) { Write-Error 'deploy_frontend.config.json: "distributionId" must not be empty.' }
if ([string]::IsNullOrWhiteSpace($awsProfile))     { Write-Error 'deploy_frontend.config.json: "awsProfile" must not be empty.' }

# Fail fast if the profile is not configured (requires AWS CLI v2)
$existingProfiles = @(aws configure list-profiles)
if ($existingProfiles -notcontains $awsProfile) {
    Write-Error "AWS profile '$awsProfile' not found. Create it with: aws configure --profile $awsProfile"
}

$frontendDir = Join-Path $PSScriptRoot '..\frontend'
$distDir     = Join-Path $frontendDir 'dist'

Write-Host '==> Building frontend...' -ForegroundColor Cyan
Push-Location $frontendDir
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'npm run build failed.' }
}
finally {
    Pop-Location
}

if (-not (Test-Path $distDir)) {
    Write-Error "Build output not found: $distDir"
}

Write-Host "==> Syncing immutable assets (profile: $awsProfile)..." -ForegroundColor Cyan
aws s3 sync "$distDir\" "s3://$bucketName" --delete `
    --profile $awsProfile `
    --cache-control "public,max-age=31536000,immutable" `
    --exclude "index.html"
if ($LASTEXITCODE -ne 0) { throw 'S3 sync failed.' }

Write-Host '==> Uploading index.html (no-cache)...' -ForegroundColor Cyan
aws s3 cp "$distDir\index.html" "s3://$bucketName/index.html" `
    --profile $awsProfile `
    --content-type "text/html; charset=utf-8" `
    --cache-control "no-cache"
if ($LASTEXITCODE -ne 0) { throw 'index.html upload failed.' }

Write-Host '==> Invalidating CloudFront...' -ForegroundColor Cyan
aws cloudfront create-invalidation `
    --profile $awsProfile `
    --distribution-id $distributionId `
    --paths "/index.html" "/"
if ($LASTEXITCODE -ne 0) { throw 'CloudFront invalidation failed.' }

Write-Host '==> Deploy complete.' -ForegroundColor Green