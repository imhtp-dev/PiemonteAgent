#!/bin/bash
# Automated Docker Build and Deploy Script
# Handles versioning and ensures latest image is always deployed

set -e  # Exit on error

# Configuration
REGISTRY="rudyimhtpdev"  # Your Docker Hub username
IMAGE_NAME="lombardia_region"
FULL_IMAGE_NAME="${REGISTRY}/${IMAGE_NAME}"

# Generate unique version tag
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
VERSION_TAG="v${TIMESTAMP}-${GIT_HASH}"

echo "🚀 Lombardia Healthcare Agent Docker Deployment"
echo "=================================="
echo "📋 Image: ${FULL_IMAGE_NAME}"
echo "🏷️  Version: ${VERSION_TAG}"
echo "⏰ Timestamp: $(date)"
echo "=================================="

# Step 1: Build with both version tag and latest
echo "📦 Building Docker image..."
docker build -t "${FULL_IMAGE_NAME}:${VERSION_TAG}" .
docker tag "${FULL_IMAGE_NAME}:${VERSION_TAG}" "${FULL_IMAGE_NAME}:latest"

echo "✅ Built: ${FULL_IMAGE_NAME}:${VERSION_TAG}"
echo "✅ Tagged: ${FULL_IMAGE_NAME}:latest"

# Step 2: Push both tags
echo "📤 Pushing to Docker Hub..."
docker push "${FULL_IMAGE_NAME}:${VERSION_TAG}"
docker push "${FULL_IMAGE_NAME}:latest"

echo "✅ Pushed: ${FULL_IMAGE_NAME}:${VERSION_TAG}"
echo "✅ Pushed: ${FULL_IMAGE_NAME}:latest"

# Step 3: Generate deployment command for Azure VM
echo ""
echo "🔧 Azure VM Deployment Commands:"
echo "=================================="
echo "# Copy and run these commands on your Azure VM:"
echo ""
echo "# Pull latest image (force update)"
echo "docker pull ${FULL_IMAGE_NAME}:latest"
echo ""
echo "# Stop and remove old container"
echo "docker-compose down || docker stop healthcare-agent || true"
echo "docker rm healthcare-agent || true"
echo ""
echo "# Start with latest image"
echo "docker-compose up -d"
echo ""
echo "# Or run directly:"
echo "docker run -d --name healthcare-agent -p 8000:8000 ${FULL_IMAGE_NAME}:latest"
echo ""
echo "🎯 Version deployed: ${VERSION_TAG}"
echo "📅 Deploy date: $(date)"

# Step 4: Save deployment info
echo "📝 Saving deployment info..."
cat > deployment-info.txt << EOF
Last Deployment Information
==========================
Image: ${FULL_IMAGE_NAME}
Version: ${VERSION_TAG}
Latest Tag: ${FULL_IMAGE_NAME}:latest
Build Date: $(date)
Git Hash: ${GIT_HASH}

Azure VM Commands:
docker pull ${FULL_IMAGE_NAME}:latest
docker-compose down
docker-compose up -d

Status Check:
docker logs healthcare-agent
curl http://localhost:8000/health
EOF

echo "✅ Deployment info saved to deployment-info.txt"
echo ""
echo "🎉 Build and push complete!"
echo "💡 Now run the Azure VM commands above to deploy"