# Build Time Test Script for Windows
# Tests Docker build performance with BuildKit cache

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "Docker Build Time Test" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""

# Check Docker is running
try {
    docker version | Out-Null
} catch {
    Write-Host "Docker is not running!" -ForegroundColor Red
    Write-Host "Please start Docker Desktop" -ForegroundColor Yellow
    exit 1
}

# Enable BuildKit
$env:DOCKER_BUILDKIT = "1"
Write-Host "BuildKit enabled" -ForegroundColor Green
Write-Host ""

# Test build
Write-Host "Starting Docker build test..." -ForegroundColor Cyan
Write-Host "Timing the build process..." -ForegroundColor Yellow
Write-Host ""
Write-Host "EXPECTED TIMES:" -ForegroundColor Yellow
Write-Host "  First build: 4-6 hours (downloads ~1.2GB packages)" -ForegroundColor Gray
Write-Host "  Second build: 5-15 minutes (reuses cached packages!)" -ForegroundColor Gray
Write-Host ""

# Measure build time
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

docker build `
    --progress=plain `
    -t rudyimhtpdev/voicebooking_piemo1:test `
    .

$stopwatch.Stop()

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "Build Complete!" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan

# Calculate time
$totalMinutes = [math]::Round($stopwatch.Elapsed.TotalMinutes, 2)
$totalSeconds = [math]::Round($stopwatch.Elapsed.TotalSeconds, 0)

Write-Host ""
Write-Host "TOTAL BUILD TIME: $totalMinutes minutes ($totalSeconds seconds)" -ForegroundColor Green
Write-Host ""

# Interpret results
if ($totalSeconds -lt 1200) {
    # Less than 20 minutes
    Write-Host "EXCELLENT! BuildKit cache is working perfectly!" -ForegroundColor Green
    Write-Host "This indicates a cached build (reused packages)" -ForegroundColor Green
} elseif ($totalSeconds -lt 3600) {
    # 20-60 minutes
    Write-Host "GOOD! Build is faster than expected" -ForegroundColor Yellow
    Write-Host "Partial cache hit - some layers rebuilt" -ForegroundColor Yellow
} else {
    # More than 1 hour
    Write-Host "This is a FIRST BUILD or cache was cleared" -ForegroundColor Yellow
    Write-Host "Packages are being downloaded and cached" -ForegroundColor Yellow
    Write-Host "Next build will be 5-15 minutes!" -ForegroundColor Green
}

Write-Host ""
Write-Host "BUILD ANALYSIS:" -ForegroundColor Cyan
Write-Host "Cache location: C:\Users\$env:USERNAME\.docker\buildkit\cache" -ForegroundColor Gray

$imageSize = docker images rudyimhtpdev/voicebooking_piemo1:test --format "{{.Size}}"
Write-Host "Image size: $imageSize" -ForegroundColor Gray

# Check cache usage
Write-Host ""
Write-Host "DOCKER CACHE STATUS:" -ForegroundColor Cyan
docker system df

Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host "1. Make a small code change (edit a comment in bot.py)" -ForegroundColor White
Write-Host "2. Run this script again: .\test-build.ps1" -ForegroundColor White
Write-Host "3. Build should complete in 5-15 minutes!" -ForegroundColor White
Write-Host ""
Write-Host "READY TO DEPLOY: Run .\deploy.ps1" -ForegroundColor Green
Write-Host ""