#!/usr/bin/env python3
"""
cron_expire.py — Désactive automatiquement les peers dont l'abonnement a expiré.
                 Envoie aussi un rappel par email J-3 avant expiration.
Ajouter dans crontab :  0 8 * * * python3 /opt/vpn-billing/cron_expire.py >> /var/log/vpn_expire.log 2>&1
"""

import sqlite3
import subprocess
import smtplib
import ssl
import urllib.request
import urllib.parse
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, timedelta

DB_PATH   = "/opt/vpn-billing/vpn_billing.db"
CONTAINER = "amnezia-awg"

def send_reminder_email(conn, to_email, nom, date_fin_str):
    """Envoie un email de rappel J-3. Lit les settings SMTP depuis la DB."""
    if not to_email or '@' not in to_email:
        return False
    domain = to_email.split('@', 1)[1].lower()
    if '.local' in domain or '.' not in domain:
        return False
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'smtp_email'"
    ).fetchone()
    addr = row[0].strip() if row else ""
    row2 = conn.execute(
        "SELECT value FROM settings WHERE key = 'smtp_password'"
    ).fetchone()
    pwd = row2[0].strip() if row2 else ""
    if not addr or not pwd:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "⏰ Votre abonnement VPN expire dans 3 jours"
        msg['From']    = f"VPN Privé <{addr}>"
        msg['To']      = to_email
        html = (
            f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
            f"<h2 style='color:#e94560'>⏰ Rappel d'expiration</h2>"
            f"<p>Bonjour <strong>{nom}</strong>,</p>"
            f"<p>Votre abonnement VPN expire le <strong>{date_fin_str}</strong>.</p>"
            f"<p>Effectuez votre renouvellement avant cette date pour ne pas perdre votre accès.</p>"
            f"<hr><small style='color:#888'>VPN Privé — Service personnel</small></div>"
        )
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx) as srv:
            srv.login(addr, pwd)
            srv.sendmail(addr, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[rappel email] Erreur → {to_email}: {e}")
        return False

def send_telegram_admin(conn, message):
    """Envoie un message sur le canal Telegram admin."""
    row_token   = conn.execute("SELECT value FROM settings WHERE key='telegram_bot_token'").fetchone()
    row_chat    = conn.execute("SELECT value FROM settings WHERE key='telegram_chat_id'").fetchone()
    token   = row_token[0].strip()   if row_token   else ""
    chat_id = row_chat[0].strip()    if row_chat    else ""
    if not token or not chat_id:
        return False
    try:
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[telegram] Erreur : {e}")
        return False


def main():
    now = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Cherche les abonnements expirés avec des peers encore actifs
    expired = c.execute("""
        SELECT p.id, p.ip_vpn, p.label, p.vpn_type, u.nom, a.date_fin
        FROM peers p
        JOIN abonnements a ON a.user_id = p.user_id
        JOIN users u ON u.id = p.user_id
        WHERE p.actif = 1
          AND a.date_fin IS NOT NULL
          AND a.date_fin < ?
    """, (now,)).fetchall()

    if not expired:
        print(f"[{now}] Aucun peer à désactiver.")
        conn.close()
        return

    for peer in expired:
        try:
            ip = peer["ip_vpn"].split("/")[0]
            vpn_type = peer["vpn_type"] or "amnezia"
            if vpn_type == "pivpn":
                # PiVPN — iptables sur le host directement
                r1 = subprocess.run(["iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"], capture_output=True, text=True)
                r2 = subprocess.run(["iptables", "-I", "FORWARD", "-d", ip, "-j", "DROP"], capture_output=True, text=True)
            else:
                # AmneziaVPN — docker exec
                r1 = subprocess.run(["docker", "exec", CONTAINER, "iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"], capture_output=True, text=True)
                r2 = subprocess.run(["docker", "exec", CONTAINER, "iptables", "-I", "FORWARD", "-d", ip, "-j", "DROP"], capture_output=True, text=True)
            if r1.returncode == 0 and r2.returncode == 0:
                c.execute("UPDATE peers SET actif = 0 WHERE id = ?", (peer["id"],))
                print(f"[{now}] ✅ Désactivé [{vpn_type}] : {peer['nom']} / {peer['label']} ({peer['ip_vpn']})")
            else:
                err = r1.stderr.strip() or r2.stderr.strip()
                print(f"[{now}] ⚠  Erreur iptables pour {peer['nom']} / {peer['label']} : {err}")
        except Exception as e:
            print(f"[{now}] ❌ Exception pour {peer['nom']} : {e}")

    # Met à jour le statut des abonnements expirés
    c.execute("""
        UPDATE abonnements SET statut = 'expire'
        WHERE date_fin < ? AND statut = 'actif'
    """, (now,))

    conn.commit()

    # ── Rappels J-3 ──────────────────────────────────────────────────────────
    j3 = (date.today() + timedelta(days=3)).isoformat()
    reminders = c.execute("""
        SELECT u.nom, u.email, u.telegram AS telegram_handle, a.date_fin
        FROM abonnements a
        JOIN users u ON u.id = a.user_id
        WHERE a.statut = 'actif'
          AND a.date_fin = ?
    """, (j3,)).fetchall()

    email_ok = 0
    for r in reminders:
        sent = send_reminder_email(conn, r["email"], r["nom"], r["date_fin"])
        if sent:
            email_ok += 1
        print(f"[{now}] Rappel J-3 email → {r['nom']} ({r['email']}) : {'✅' if sent else '⏭ skipped'}")

    # ── Rappel Telegram (canal admin) — liste des expirations J-3 ────────────
    if reminders:
        lignes = "\n".join(
            f"  • <b>{r['nom']}</b>"
            + (f" (@{r['telegram_handle']})" if r.get('telegram_handle') else "")
            + f" — expire le {r['date_fin']}"
            for r in reminders
        )
        tg_msg = (
            f"⏰ <b>Rappel J-3 VPN</b>\n"
            f"{len(reminders)} abonnement(s) expirent dans 3 jours :\n\n"
            f"{lignes}"
        )
        tg_sent = send_telegram_admin(conn, tg_msg)
        print(f"[{now}] Rappel J-3 Telegram admin : {'✅' if tg_sent else '⏭ skipped (token/chat_id manquant?)'}")

    conn.close()
    print(f"[{now}] Terminé — {len(expired)} peer(s) traité(s), {len(reminders)} rappel(s) J-3 ({email_ok} emails).")

if __name__ == "__main__":
    main()
