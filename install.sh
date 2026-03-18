#!/bin/bash
# install.sh — Déploiement complet sur le VPS
# Lancer en root : bash install.sh

set -e

echo "=== Déploiement VPN Billing ==="

# 1. Copier les fichiers
mkdir -p /opt/vpn-billing/templates
cp app.py init_db.py cron_expire.py requirements.txt /opt/vpn-billing/
cp templates/*.html /opt/vpn-billing/templates/

# 2. Installer Python + Flask
apt-get install -y python3 python3-pip python3-venv nginx 2>/dev/null || true
cd /opt/vpn-billing
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 3. Initialiser la base de données
venv/bin/python3 init_db.py

# 4. Cron job (toutes les heures)
(crontab -l 2>/dev/null; echo "0 * * * * /opt/vpn-billing/venv/bin/python3 /opt/vpn-billing/cron_expire.py >> /var/log/vpn_expire.log 2>&1") | crontab -

# 5. Service systemd
cat > /etc/systemd/system/vpn-billing.service << 'EOF'
[Unit]
Description=VPN Billing Portal
After=network.target

[Service]
WorkingDirectory=/opt/vpn-billing
ExecStart=/opt/vpn-billing/venv/bin/python3 app.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vpn-billing
systemctl start vpn-billing

# 6. Nginx reverse proxy (HTTP uniquement — lancer setup_https.sh pour HTTPS)
cat > /etc/nginx/sites-available/vpn-billing << 'EOF'
server {
    listen 80;
    server_name benusvpn.duckdns.org;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/vpn-billing /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "✅ Installation terminée !"
echo "   Accès : http://$(curl -s ifconfig.me)"
echo "   Admin : admin@vpn.local / admin1234"
echo "   ⚠  Change le mot de passe admin immédiatement !"
