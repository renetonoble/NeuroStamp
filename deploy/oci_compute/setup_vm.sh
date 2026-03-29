#!/bin/bash
# Setup script for NeuroStamp on an OCI Compute instance (Ubuntu 22.04 LTS recommended).
# Run as a user with sudo privileges (ubuntu/opc). Edit PROJECT_DIR to match where you clone the repo.
set -euo pipefail

PROJECT_DIR=/home/ubuntu/NeuroStamp
PYTHON=python3.11
VENV_DIR=$PROJECT_DIR/venv
GUNICORN_SERVICE=/etc/systemd/system/neurostamp.service
NGINX_CONF=/etc/nginx/sites-available/neurostamp

echo "Updating apt and installing prerequisites..."
sudo apt-get update
sudo apt-get install -y git $PYTHON $PYTHON-venv $PYTHON-dev build-essential libpq-dev nginx certbot python3-certbot-nginx

echo "Cloning project (if not already present)..."
if [ ! -d "$PROJECT_DIR" ]; then
  sudo -u ubuntu git clone https://github.com/DrNotSoStrange05/NeuroStamp.git $PROJECT_DIR
fi

echo "Creating Python virtual environment..."
$PYTHON -m venv $VENV_DIR
source $VENV_DIR/bin/activate
pip install --upgrade pip
pip install -r $PROJECT_DIR/requirements.txt

echo "Ensure static/uploads directory exists..."
sudo -u ubuntu mkdir -p $PROJECT_DIR/static/uploads
sudo chown -R ubuntu:ubuntu $PROJECT_DIR

# Create systemd service
sudo bash -c "cat > $GUNICORN_SERVICE <<'SERVICE'
[Unit]
Description=NeuroStamp Gunicorn Uvicorn service
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$VENV_DIR/bin"
ExecStart=$VENV_DIR/bin/gunicorn main:app -k uvicorn.workers.UvicornWorker -b 127.0.0.1:8000 --workers 3 --log-level info
Restart=on-failure

[Install]
WantedBy=multi-user.target
SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable --now neurostamp.service

# Configure Nginx
sudo bash -c "cat > $NGINX_CONF <<'NGINX'
server {
    listen 80;
    server_name _; # replace with your domain or IP

    client_max_body_size 50M;

    location /static/ {
        alias $PROJECT_DIR/static/;
        access_log off;
        expires 30d;
    }

    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass http://127.0.0.1:8000;
        proxy_read_timeout 300;
    }
}
NGINX"

sudo ln -sf $NGINX_CONF /etc/nginx/sites-enabled/neurostamp
sudo nginx -t
sudo systemctl restart nginx

echo "If you have a domain, use certbot to obtain TLS certs (example):"
echo "sudo certbot --nginx -d yourdomain.example.com"

echo "Deployment complete. Check service status: sudo systemctl status neurostamp.service"
