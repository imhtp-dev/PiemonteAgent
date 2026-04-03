#!/bin/bash
# Run this ON the staging VM (20.19.88.192) to set it up
# Usage: ssh azureuser@20.19.88.192 'bash -s' < scripts/setup-staging-vm.sh

set -e

DOMAIN="piemonte-staging.francecentral.cloudapp.azure.com"

echo "🚀 Setting up Piemonte Agent STAGING VM"
echo "========================================="

# 1. Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "📦 Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker azureuser
    echo "✅ Docker installed. You may need to log out and back in."
else
    echo "✅ Docker already installed"
fi

# 2. Install Docker Compose plugin if not present
if ! docker compose version &> /dev/null; then
    echo "📦 Installing Docker Compose plugin..."
    sudo apt-get update && sudo apt-get install -y docker-compose-plugin
else
    echo "✅ Docker Compose already installed"
fi

# 3. Install certbot for SSL
if ! command -v certbot &> /dev/null; then
    echo "🔐 Installing certbot..."
    sudo apt-get update
    sudo apt-get install -y certbot
else
    echo "✅ Certbot already installed"
fi

# 4. Get SSL certificate
echo "🔐 Getting SSL certificate for $DOMAIN..."
echo "   Make sure port 80 is open in Azure NSG and DNS label is set!"
sudo certbot certonly --standalone \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email admin@cerbahealthcare.it \
    --preferred-challenges http

echo "✅ SSL certificate obtained"

# 5. Set up auto-renewal
echo "🔄 Setting up SSL auto-renewal..."
(crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet --deploy-hook 'docker restart nginx-load-balancer'") | sort -u | crontab -
echo "✅ Auto-renewal cron job added"

# 6. Clone repo
if [ ! -d /home/azureuser/voilavoiceagent ]; then
    echo "📥 Cloning repository..."
    cd /home/azureuser
    git clone https://github.com/imhtp-dev/PiemonteAgent.git voilavoiceagent
    cd voilavoiceagent
    git checkout staging
else
    echo "✅ Repository already exists"
    cd /home/azureuser/voilavoiceagent
    git fetch origin
    git checkout staging
    git pull origin staging
fi

# 7. Create directories
mkdir -p logs/nginx recordings

# 8. Reminder
echo ""
echo "========================================="
echo "✅ STAGING VM SETUP COMPLETE"
echo "========================================="
echo ""
echo "📋 Next steps:"
echo "   1. Copy .env file to /home/azureuser/voilavoiceagent/.env"
echo "   2. Run: docker compose -f docker-compose.staging.yml up -d"
echo "   3. Test: curl http://localhost:8000/health"
echo "   4. Test SSL: curl https://$DOMAIN/health"
echo ""
echo "📞 Talkdesk URL to share:"
echo "   wss://$DOMAIN/talkdesk"
echo ""
