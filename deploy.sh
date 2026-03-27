#!/bin/bash
# Deploy script for agents on EC2
# Run as root or with sudo

set -e

echo "Starting agent deployment..."

# Install system dependencies
apt update
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx

# Create .secrets directory
mkdir -p /app/.secrets

# Copy systemd files
cp /home/ubuntu/app/systemd/teams-agent.service /etc/systemd/system/
cp /home/ubuntu/app/systemd/todo-agent.service /etc/systemd/system/

# Create virtual environments and install dependencies
cd /home/ubuntu/app/agents/teams-agent
python3 -m venv venv
venv/bin/pip install -r requirements.txt

cd /home/ubuntu/app/agents/todo-agent
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Copy nginx config
cp /home/ubuntu/app/nginx/sites-available/agents /etc/nginx/sites-available/

# Enable sites
ln -sf /etc/nginx/sites-available/agents /etc/nginx/sites-enabled/

# Test nginx config
nginx -t

# Reload nginx
systemctl reload nginx

# Enable and start services
systemctl enable teams-agent
systemctl enable todo-agent
systemctl start teams-agent
systemctl start todo-agent

# Check status
systemctl status teams-agent --no-pager
systemctl status todo-agent --no-pager

# Run certbot (replace with your domains)
# certbot --nginx -d teams.yourdomain.com -d todo.yourdomain.com

echo "Deployment complete. Run certbot manually for SSL."
echo "Teams agent: http://teams.yourdomain.com"
echo "Todo agent: http://todo.yourdomain.com"