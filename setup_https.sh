#!/bin/bash
# =============================================================================
# setup_https.sh — Configure HTTPS (Let's Encrypt) pour benusvpn.duckdns.org
# Lancer en root sur le VPS : bash setup_https.sh
# =============================================================================
set -e

DOMAIN="benusvpn.duckdns.org"
EMAIL="benuslavision@gmail.com"   # pour les alertes d'expiration Let's Encrypt

echo "=== Configuration HTTPS pour $DOMAIN ==="

# 1. Installer Certbot + plugin Nginx
echo "[1/5] Installation de Certbot..."
apt-get update -qq
apt-get install -y certbot python3-certbot-nginx

# 2. Mettre à jour la config Nginx avec le vrai nom de domaine
echo "[2/5] Mise à jour de la config Nginx..."
cat > /etc/nginx/sites-available/vpn-billing << EOF
server {
    listen 80;
    server_name $DOMAIN;

    # Laisser Certbot gérer la validation ACME
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    # Redirection HTTP → HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name $DOMAIN;

    # Certificats (remplis par Certbot)
    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    # Paramètres de sécurité
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;

    # Proxy vers Flask
    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF

# Tester la config Nginx avant d'aller plus loin
nginx -t
systemctl reload nginx

# 3. Obtenir le certificat Let's Encrypt
echo "[3/5] Obtention du certificat SSL..."
certbot --nginx \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --redirect

# 4. Vérifier le renouvellement automatique
echo "[4/5] Vérification du renouvellement automatique..."
systemctl enable certbot.timer 2>/dev/null || true
systemctl start  certbot.timer 2>/dev/null || true
# Fallback : ajouter un cron si le timer systemd n'existe pas
if ! systemctl is-active certbot.timer &>/dev/null; then
    (crontab -l 2>/dev/null | grep -v certbot; echo "0 3 * * * certbot renew --quiet && systemctl reload nginx") | crontab -
    echo "   → Renouvellement via cron (3h du matin chaque jour)"
else
    echo "   → Renouvellement via systemd timer ✅"
fi

# 5. Recharger Nginx avec la config finale
echo "[5/5] Rechargement Nginx..."
systemctl reload nginx

echo ""
echo "✅ HTTPS configuré avec succès !"
echo "   URL : https://$DOMAIN"
echo ""
echo "⚠️  N'oublie pas de mettre à jour dans l'admin du portail :"
echo "   Paramètres → URL publique du site → https://$DOMAIN"
echo ""
echo "   Test du renouvellement : certbot renew --dry-run"
