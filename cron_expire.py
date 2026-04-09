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

def get_settings(conn):
    """Lit tous les settings depuis la DB."""
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r[0]: (r[1] or "").strip() for r in rows}

def send_reminder_email(conn, to_email, nom, date_fin_str, subject=None, html_body=None):
    """Envoie un email de rappel via API Brevo (HTTP) ou SMTP en fallback."""
    if not to_email or '@' not in to_email:
        return False
    domain = to_email.split('@', 1)[1].lower()
    if '.local' in domain or '.' not in domain:
        return False
    if subject is None:
        subject = "⏰ Votre accès expire dans 3 jours"
    if html_body is None:
        html_body = (
            f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
            f"<h2 style='color:#e94560'>⏰ Rappel d'expiration</h2>"
            f"<p>Bonjour <strong>{nom}</strong>,</p>"
            f"<p>Votre accès expire le <strong>{date_fin_str}</strong>.</p>"
            f"<p>Contactez-nous pour renouveler avant cette date et éviter toute interruption.</p>"
            f"<hr><small style='color:#888'>SP Network — Service personnel</small></div>"
        )
    s         = get_settings(conn)
    from_addr = s.get("smtp_email", "")
    api_key   = s.get("brevo_api_key", "")
    if not from_addr:
        return False

    # ── Voie 1 : API HTTP Brevo ───────────────────────────────────────────────
    if api_key:
        try:
            payload = json.dumps({
                "sender":      {"name": "SP Network", "email": from_addr},
                "to":          [{"email": to_email}],
                "subject":     subject,
                "htmlContent": html_body,
            }).encode()
            req = urllib.request.Request(
                "https://api.brevo.com/v3/smtp/email",
                data=payload,
                headers={
                    "api-key":      api_key,
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                },
            )
            urllib.request.urlopen(req, timeout=15)
            return True
        except Exception as e:
            print(f"[rappel email] Erreur API Brevo → {to_email}: {e}")
            return False

    # ── Voie 2 : SMTP fallback ────────────────────────────────────────────────
    host  = s.get("smtp_host", "smtp-relay.brevo.com") or "smtp-relay.brevo.com"
    port  = int(s.get("smtp_port", "587") or 587)
    login = s.get("smtp_username") or from_addr
    pwd   = s.get("smtp_password", "")
    if not pwd:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f"SP Network <{from_addr}>"
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as srv:
            srv.ehlo(); srv.starttls(context=ctx); srv.ehlo()
            srv.login(login, pwd)
            srv.sendmail(from_addr, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[rappel email] Erreur SMTP → {to_email}: {e}")
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

    # ── Email + Telegram admin pour chaque suspension ─────────────────────────
    for peer in expired:
        user = conn.execute(
            "SELECT u.id, u.nom, u.email, u.telegram FROM users u WHERE u.nom = ?",
            (peer["nom"],)
        ).fetchone()
        if not user:
            continue

        # Email au client
        html_suspension = (
            f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
            f"<h2 style='color:#e94560'>⏸ Ton accès a été suspendu</h2>"
            f"<p>Bonjour <strong>{user['nom']}</strong>,</p>"
            f"<p>Ton abonnement a expiré le <strong>{peer['date_fin']}</strong>.<br>"
            f"Ton accès a été suspendu.</p>"
            f"<p>Contacte-nous pour le renouveler.</p>"
            f"<hr><small style='color:#888'>— L'équipe Network Privé</small></div>"
        )
        send_reminder_email(
            conn, user["email"], user["nom"], peer["date_fin"],
            subject="⏸ Ton accès a été suspendu",
            html_body=html_suspension
        )

        # Notification Telegram admin
        contact = f"@{user['telegram']}" if user.get("telegram") else "—"
        send_telegram_admin(conn,
            f"🔴 <b>Suspension automatique</b>\n\n"
            f"👤 {user['nom']} — {user['email']}\n"
            f"📅 Expiré le : {peer['date_fin']}\n"
            f"💬 Telegram : {contact}\n\n"
            f"Pour réactiver : /reactiver_{user['id']}"
        )

    # ── Rappels J-3 ──────────────────────────────────────────────────────────
    j3 = (date.today() + timedelta(days=3)).isoformat()
    reminders_j3 = c.execute("""
        SELECT a.user_id, u.nom, u.email, u.telegram AS telegram_handle, a.date_fin
        FROM abonnements a
        JOIN users u ON u.id = a.user_id
        WHERE a.statut = 'actif'
          AND a.date_fin = ?
          AND (a.reminded_j3 IS NULL OR a.reminded_j3 = 0)
    """, (j3,)).fetchall()

    email_j3_ok = 0
    for r in reminders_j3:
        html_j3 = (
            f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
            f"<h2 style='color:#e94560'>⏰ Ton accès expire dans 3 jours</h2>"
            f"<p>Bonjour <strong>{r['nom']}</strong>,</p>"
            f"<p>Ton accès expire le <strong>{r['date_fin']}</strong>.</p>"
            f"<p>Pour continuer sans interruption,<br>"
            f"contacte-nous pour renouveler ton abonnement.</p>"
            f"<hr><small style='color:#888'>— L'équipe Network Privé</small></div>"
        )
        sent = send_reminder_email(
            conn, r["email"], r["nom"], r["date_fin"],
            subject="⏰ Ton accès expire dans 3 jours",
            html_body=html_j3
        )
        if sent:
            email_j3_ok += 1
            c.execute("UPDATE abonnements SET reminded_j3 = 1 WHERE user_id = ?", (r["user_id"],))
        print(f"[{now}] Rappel J-3 → {r['nom']} ({r['email']}) : {'✅' if sent else '⏭ skipped'}")

    # ── Rappels J-1 ──────────────────────────────────────────────────────────
    j1 = (date.today() + timedelta(days=1)).isoformat()
    reminders_j1 = c.execute("""
        SELECT a.user_id, u.nom, u.email, u.telegram AS telegram_handle, a.date_fin
        FROM abonnements a
        JOIN users u ON u.id = a.user_id
        WHERE a.statut = 'actif'
          AND a.date_fin = ?
          AND (a.reminded_j1 IS NULL OR a.reminded_j1 = 0)
    """, (j1,)).fetchall()

    email_j1_ok = 0
    for r in reminders_j1:
        html_j1 = (
            f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
            f"<h2 style='color:#e94560'>⚠️ Dernier rappel — ton accès expire demain</h2>"
            f"<p>Bonjour <strong>{r['nom']}</strong>,</p>"
            f"<p>Ton accès expire demain le <strong>{r['date_fin']}</strong>.</p>"
            f"<p>Renouvelle maintenant pour éviter<br>toute interruption de service.</p>"
            f"<hr><small style='color:#888'>— L'équipe Network Privé</small></div>"
        )
        sent = send_reminder_email(
            conn, r["email"], r["nom"], r["date_fin"],
            subject="⚠️ Dernier rappel — ton accès expire demain",
            html_body=html_j1
        )
        if sent:
            email_j1_ok += 1
            c.execute("UPDATE abonnements SET reminded_j1 = 1 WHERE user_id = ?", (r["user_id"],))
        print(f"[{now}] Rappel J-1 → {r['nom']} ({r['email']}) : {'✅' if sent else '⏭ skipped'}")

    conn.commit()

    # ── Rappel Telegram admin — résumé J-3 + J-1 ─────────────────────────────
    all_reminders = list(reminders_j3) + list(reminders_j1)
    if all_reminders:
        lignes_j3 = "\n".join(
            f"  • <b>{r['nom']}</b>"
            + (f" (@{r['telegram_handle']})" if r.get('telegram_handle') else "")
            + f" — expire le {r['date_fin']}"
            for r in reminders_j3
        )
        lignes_j1 = "\n".join(
            f"  • <b>{r['nom']}</b>"
            + (f" (@{r['telegram_handle']})" if r.get('telegram_handle') else "")
            + f" — expire DEMAIN {r['date_fin']}"
            for r in reminders_j1
        )
        tg_msg = "⏰ <b>Rappels d'expiration</b>\n"
        if reminders_j3:
            tg_msg += f"\n<b>J-3</b> ({len(reminders_j3)}) :\n{lignes_j3}"
        if reminders_j1:
            tg_msg += f"\n\n<b>J-1</b> ({len(reminders_j1)}) :\n{lignes_j1}"
        tg_sent = send_telegram_admin(conn, tg_msg)
        print(f"[{now}] Rappels Telegram admin : {'✅' if tg_sent else '⏭ skipped'}")

    conn.close()
    print(f"[{now}] Terminé — {len(expired)} peer(s) traité(s), "
          f"J-3: {len(reminders_j3)} rappel(s) ({email_j3_ok} emails), "
          f"J-1: {len(reminders_j1)} rappel(s) ({email_j1_ok} emails).")

if __name__ == "__main__":
    main()
