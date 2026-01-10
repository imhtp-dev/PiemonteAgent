# PowerShell Automated Deployment Script for Windows
# Optimized Docker Build with BuildKit Cache
# 
# FIRST BUILD: downloads everything
# SUBSEQUENT BUILDS: 5-15 minutes (uses cached packages!)
# AZURE DEPLOYMENT: 30 seconds (just pulls image, NO building!)

param(
    [switch]$SkipBuild,
    [switch]$DeployToAzure,
    [string]$AzureHost = "",
    [string]$AzureUser = "",
    [int]$ScaleInstances = 1
)

# Configuration
$REGISTRY = "rudyimhtpdev"
$IMAGE_NAME = "voicebooking_piemo1"
$FULL_IMAGE_NAME = "${REGISTRY}/${IMAGE_NAME}"

# Generate version tag
$TIMESTAMP = Get-Date -Format "yyyyMMdd-HHmmss"
$GIT_HASH = "unknown"
try {
    $GIT_HASH = git rev-parse --short HEAD 2>$null
} catch {
    Write-Host "Git not available or not a git repository"
}
$VERSION_TAG = "v${TIMESTAMP}-${GIT_HASH}"

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "Healthcare Agent Docker Deployment (Windows)" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "Image: $FULL_IMAGE_NAME" -ForegroundColor Yellow
Write-Host "Version: $VERSION_TAG" -ForegroundColor Yellow
Write-Host "Timestamp: $(Get-Date)" -ForegroundColor Yellow
Write-Host "===============================================" -ForegroundColor Cyan

if (-not $SkipBuild) {
    Write-Host ""
    Write-Host "STEP 1: Building Docker Image with BuildKit Cache" -ForegroundColor Green
    Write-Host "===============================================" -ForegroundColor Cyan
    Write-Host "OPTIMIZATION: BuildKit cache will save packages locally" -ForegroundColor Yellow
    Write-Host "First build: 4-6 hours (downloads PyTorch, etc.)" -ForegroundColor Yellow
    Write-Host "Next builds: 5-15 minutes (reuses cached packages!)" -ForegroundColor Yellow
    Write-Host ""

    # Enable BuildKit for cache mount support
    $env:DOCKER_BUILDKIT = "1"
    $env:COMPOSE_DOCKER_CLI_BUILD = "1"

    # Build with BuildKit cache
    Write-Host "Building image: ${FULL_IMAGE_NAME}:${VERSION_TAG}" -ForegroundColor Cyan
    
    $buildArgs = @(
        "build",
        "--progress=plain",
        "-t", "${FULL_IMAGE_NAME}:${VERSION_TAG}",
        "-t", "${FULL_IMAGE_NAME}:latest",
        "."
    )
    
    $buildProcess = Start-Process -FilePath "docker" -ArgumentList $buildArgs -NoNewWindow -Wait -PassThru
    
    if ($buildProcess.ExitCode -ne 0) {
        Write-Host "❌ Docker build failed!" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "✅ Built: ${FULL_IMAGE_NAME}:${VERSION_TAG}" -ForegroundColor Green
    Write-Host "✅ Tagged: ${FULL_IMAGE_NAME}:latest" -ForegroundColor Green
    
    # Push to Docker Hub
    Write-Host ""
    Write-Host "📤 STEP 2: Pushing to Docker Hub" -ForegroundColor Green
    Write-Host "===============================================" -ForegroundColor Cyan
    
    docker push "${FULL_IMAGE_NAME}:${VERSION_TAG}"
    docker push "${FULL_IMAGE_NAME}:latest"
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Docker push failed!" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "✅ Pushed: ${FULL_IMAGE_NAME}:${VERSION_TAG}" -ForegroundColor Green
    Write-Host "✅ Pushed: ${FULL_IMAGE_NAME}:latest" -ForegroundColor Green
} else {
    Write-Host "⏭️  Skipping build (--SkipBuild flag set)" -ForegroundColor Yellow
}

# Local deployment commands
Write-Host ""
Write-Host "🔧 STEP 3: Local Deployment Commands" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Run these commands to deploy locally:" -ForegroundColor Yellow
Write-Host ""
Write-Host "# Start single instance:" -ForegroundColor Gray
Write-Host "docker-compose up -d" -ForegroundColor White
Write-Host ""
Write-Host "# Start with SCALING (for 15-20 concurrent calls):" -ForegroundColor Gray
Write-Host "docker-compose up -d --scale pipecat-agent=${ScaleInstances}" -ForegroundColor White
Write-Host ""
Write-Host "# Check status:" -ForegroundColor Gray
Write-Host "docker-compose ps" -ForegroundColor White
Write-Host ""
Write-Host "# View logs:" -ForegroundColor Gray
Write-Host "docker-compose logs -f pipecat-agent" -ForegroundColor White
Write-Host ""
Write-Host "# Stop all:" -ForegroundColor Gray
Write-Host "docker-compose down" -ForegroundColor White
Write-Host ""

# Azure deployment
if ($DeployToAzure -and $AzureHost -and $AzureUser) {
    Write-Host ""
    Write-Host "☁️  STEP 4: Deploying to Azure VM" -ForegroundColor Green
    Write-Host "===============================================" -ForegroundColor Cyan
    Write-Host "🌐 Host: $AzureHost" -ForegroundColor Yellow
    Write-Host "👤 User: $AzureUser" -ForegroundColor Yellow
    Write-Host ""
    
    # Create deployment commands
    $deployCommands = @"
# Pull latest image (NO building on Azure!)
docker pull ${FULL_IMAGE_NAME}:latest

# Stop old containers
docker-compose down

# Start with scaling for concurrent calls
docker-compose up -d --scale pipecat-agent=${ScaleInstances}

# Cleanup old images
docker image prune -f

# Check status
docker-compose ps
docker-compose logs --tail=50 pipecat-agent
"@
    
    Write-Host "Deploying to Azure VM..." -ForegroundColor Cyan
    
    # SSH to Azure and execute commands
    $deployCommands | ssh "${AzureUser}@${AzureHost}" "cd /path/to/app && bash -s"
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Deployed to Azure successfully!" -ForegroundColor Green
    } else {
        Write-Host "❌ Azure deployment failed!" -ForegroundColor Red
        exit 1
    }
} elseif ($DeployToAzure) {
    Write-Host "❌ Azure deployment requires -AzureHost and -AzureUser parameters" -ForegroundColor Red
    Write-Host "Example: .\deploy.ps1 -DeployToAzure -AzureHost 'your-vm-ip' -AzureUser 'azureuser'" -ForegroundColor Yellow
}

# Save deployment info
Write-Host ""
Write-Host "📝 STEP 5: Saving Deployment Info" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan

$deploymentInfo = @"
Last Deployment Information
==========================
Image: $FULL_IMAGE_NAME
Version: $VERSION_TAG
Latest Tag: ${FULL_IMAGE_NAME}:latest
Build Date: $(Get-Date)
Git Hash: $GIT_HASH
Scale Instances: $ScaleInstances

Windows Local Deployment:
  docker-compose up -d --scale pipecat-agent=$ScaleInstances

Azure VM Deployment (after SSH):
  docker pull ${FULL_IMAGE_NAME}:latest
  docker-compose down
  docker-compose up -d --scale pipecat-agent=$ScaleInstances
  docker image prune -f

Health Check:
  curl http://localhost:8000/health
  docker-compose ps
  docker-compose logs -f pipecat-agent

Scaling Examples:
  # For 10 concurrent calls
  docker-compose up -d --scale pipecat-agent=10
  
  # For 20 concurrent calls (max with port range)
  docker-compose up -d --scale pipecat-agent=20

BuildKit Cache:
  First build: 4-6 hours
  Subsequent builds: 5-15 minutes (cache reused!)
  Azure deployment: 30 seconds (pulls ready image)
"@

$deploymentInfo | Out-File -FilePath "deployment-info.txt" -Encoding UTF8

Write-Host "✅ Deployment info saved to deployment-info.txt" -ForegroundColor Green
Write-Host ""
Write-Host "🎉 Build and deployment preparation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "💡 NEXT STEPS:" -ForegroundColor Yellow
Write-Host "   1. Test locally: docker-compose up -d" -ForegroundColor White
Write-Host "   2. Test scaling: docker-compose up -d --scale pipecat-agent=5" -ForegroundColor White
Write-Host "   3. Deploy to Azure: SSH to VM and run deployment commands above" -ForegroundColor White
Write-Host ""
Write-Host "⚡ OPTIMIZATION ACHIEVED:" -ForegroundColor Green
Write-Host "   • First build: Still 4-6 hours (one-time cost)" -ForegroundColor Yellow
Write-Host "   • Next builds: 5-15 minutes (BuildKit cache magic!)" -ForegroundColor Green
Write-Host "   • Azure deploy: 30 seconds (pulls pre-built image)" -ForegroundColor Green
Write-Host "   • Scaling: docker-compose up --scale pipecat-agent=20" -ForegroundColor Green
Write-Host ""