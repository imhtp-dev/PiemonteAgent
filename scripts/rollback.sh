#!/bin/bash
# Rollback to previous deployment
# Usage: ./rollback.sh

echo "🔄 Rolling back Pipecat agents to previous version..."

# Restore backup image
docker tag rudyimhtpdev/lombardia_region:backup rudyimhtpdev/lombardia_region:latest

# Restart all pipecat agents
docker-compose up -d --no-deps pipecat-agent-1 pipecat-agent-2 pipecat-agent-3

echo "✅ Rollback complete"
echo "Checking status..."
docker-compose ps
