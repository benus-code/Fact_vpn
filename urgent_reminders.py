#!/usr/bin/env python3
"""
urgent_reminders.py — Envoi forcé de rappels pour les abonnements expirant aujourd'hui.
Usage : python3 /opt/vpn-billing/urgent_reminders.py
"""
import sqlite3
import sys
sys.path.insert(0, '/opt/vpn-billing')
from cron_expire import send_reminder_email, get_settings

DB_PATH = '/opt/vpn-billing/vpn_billing.db'

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT a.id AS abo_id, a.date_fin, a.reminded_j1,
               u.nom, u.email
        FROM abonnements a
        JOIN users u ON u.id = a.user_id
        WHERE a.statut = 'actif'
          AND date(a.date_fin) = date('now')
          AND (a.reminded_j1 IS NULL OR a.reminded_j1 = 0)
    """).fetchall()

    if not rows:
        print("Aucun client expirant aujourd'hui sans rappel J-1.")
        conn.close()
        return

    for r in rows:
        print(f"Envoi urgent → {r['nom']} ({r['email']}) expire le {r['date_fin']}...")
        html = (
            f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
            f"<h2 style='color:#e94560'>⚠️ Ton accès expire aujourd'hui</h2>"
            f"<p>Bonjour <strong>{r['nom']}</strong>,</p>"
            f"<p>Ton abonnement expire <strong>aujourd'hui le {r['date_fin']}</strong>.</p>"
            f"<p>Contacte-nous dès maintenant pour éviter toute interruption.</p>"
            f"<hr><small style='color:#888'>— L'équipe Network Privé</small></div>"
        )
        sent = send_reminder_email(
            conn, r['email'], r['nom'], r['date_fin'],
            subject="⚠️ Ton accès expire aujourd'hui — agis maintenant",
            html_body=html
        )
        if sent:
            conn.execute(
                "UPDATE abonnements SET reminded_j1 = 1 WHERE id = ?",
                (r['abo_id'],)
            )
            conn.commit()
            print(f"  ✅ Email envoyé et reminded_j1 mis à 1")
        else:
            print(f"  ⏭ Email non envoyé (adresse invalide ou erreur SMTP)")

    conn.close()

if __name__ == '__main__':
    main()
