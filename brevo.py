"""
brevo.py — Wrapper API Brevo + intégration SQLite.

Endpoints utilisés :
  GET  /v3/account                           → crédits + plan
  GET  /v3/smtp/statistics/aggregatedReport  → KPIs globaux
  GET  /v3/smtp/statistics/events            → events par email
"""
import os
import sqlite3
import logging
import requests
from datetime import datetime, timedelta

DB = "/opt/vpn-billing/vpn_billing.db"
BREVO_BASE = "https://api.brevo.com/v3"
TIMEOUT = 10

log = logging.getLogger("brevo")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def get_api_key():
    """Lit la clé depuis SQLite settings, fallback env BREVO_API_KEY."""
    try:
        c = _db().cursor()
        c.execute("SELECT value FROM settings WHERE key='brevo_api_key'")
        row = c.fetchone()
        if row and row[0]:
            return row[0].strip()
    except Exception:
        pass
    return os.environ.get("BREVO_API_KEY", "").strip()


def _headers():
    key = get_api_key()
    if not key:
        return None
    return {"api-key": key, "accept": "application/json", "content-type": "application/json"}


def _log_error(message, file_line=""):
    try:
        c = _db()
        c.execute(
            "INSERT INTO error_logs (level, source, message, file_line) VALUES ('error', 'brevo', ?, ?)",
            (str(message)[:500], file_line),
        )
        c.commit()
        c.close()
    except Exception:
        pass


# ─── API calls ────────────────────────────────────────────────────────────────

def get_account():
    """Retourne dict avec email_credits et plan, ou None si erreur / clé absente."""
    h = _headers()
    if not h:
        return None
    try:
        r = requests.get(f"{BREVO_BASE}/account", headers=h, timeout=TIMEOUT)
        if r.status_code != 200:
            _log_error(f"GET /account → {r.status_code}: {r.text[:200]}", "brevo.py:get_account")
            return None
        d = r.json()
        email_credits = 0
        for p in d.get("plan", []):
            if p.get("type") in ("payAsYouGo", "salesPayAsYouGo", "free", "monthly"):
                email_credits += p.get("credits", 0)
        return {
            "email": d.get("email"),
            "email_credits": email_credits,
            "plan": d.get("plan", []),
        }
    except Exception as e:
        _log_error(f"get_account: {e}", "brevo.py:get_account")
        return None


def get_aggregated_stats(days=30):
    """
    Retourne dict avec requests, delivered, opens, hardBounces, softBounces, etc.
    Ou None si erreur.
    """
    h = _headers()
    if not h:
        return None
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"{BREVO_BASE}/smtp/statistics/aggregatedReport",
            headers=h,
            params={"startDate": start, "endDate": end},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            _log_error(f"aggregatedReport → {r.status_code}", "brevo.py:get_aggregated_stats")
            return None
        return r.json()
    except Exception as e:
        _log_error(f"get_aggregated_stats: {e}", "brevo.py:get_aggregated_stats")
        return None


def get_events_for_email(email, days=90, limit=50):
    """Retourne la liste des événements Brevo pour un destinataire."""
    h = _headers()
    if not h:
        return []
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"{BREVO_BASE}/smtp/statistics/events",
            headers=h,
            params={"email": email, "startDate": start, "endDate": end, "limit": limit},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return []
        return r.json().get("events", [])
    except Exception as e:
        _log_error(f"get_events_for_email({email}): {e}", "brevo.py:get_events_for_email")
        return []


def get_kpis():
    """
    KPIs synthétiques pour /admin/messages et /admin/monitoring.
    Toujours retourne un dict complet (valeurs = 0 si Brevo non configuré).
    """
    stats = get_aggregated_stats(30) or {}
    account = get_account() or {}

    requests_30  = int(stats.get("requests", 0) or 0)
    delivered_30 = int(stats.get("delivered", 0) or 0)
    opens_30     = int(stats.get("opens", 0) or 0)
    hard_b       = int(stats.get("hardBounces", 0) or 0)
    soft_b       = int(stats.get("softBounces", 0) or 0)

    bounce_rate = round((hard_b + soft_b) / requests_30 * 100, 2) if requests_30 else 0
    open_rate   = round(opens_30 / delivered_30 * 100, 1) if delivered_30 else 0

    return {
        "api_ok":           bool(account),
        "credits_restants": account.get("email_credits", 0),
        "envoyes_30j":      requests_30,
        "delivres_30j":     delivered_30,
        "taux_ouverture":   open_rate,
        "taux_bounce":      bounce_rate,
        "hard_bounces":     hard_b,
        "soft_bounces":     soft_b,
    }


# ─── SQLite helpers ───────────────────────────────────────────────────────────

def log_email(user_id, to_email, subject, template_type,
              brevo_msg_id=None, status="sent", error=None):
    """Trace chaque envoi dans email_logs."""
    try:
        c = _db()
        c.execute(
            "INSERT INTO email_logs "
            "(user_id, to_email, subject, template_type, brevo_message_id, status, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, to_email, str(subject)[:250], template_type, brevo_msg_id, status,
             str(error)[:500] if error else None),
        )
        c.commit()
        c.close()
    except Exception as e:
        log.warning("brevo.log_email failed: %s", e)


def get_status_for_user(user_id):
    """
    Retourne (color, label, last_at) basé sur le dernier email_logs de cet user.
    color: 'green' | 'orange' | 'red' | 'gray'
    """
    try:
        c = _db().cursor()
        c.execute(
            "SELECT status, last_event_at, sent_at FROM email_logs "
            "WHERE user_id=? ORDER BY sent_at DESC LIMIT 1",
            (user_id,),
        )
        row = c.fetchone()
    except Exception:
        return ("gray", "erreur BDD", None)

    if not row:
        return ("gray", "jamais", None)

    status, last_event, sent_at = row["status"], row["last_event_at"], row["sent_at"]
    when = last_event or sent_at

    if status in ("opened", "clicked"):
        return ("green", status, when)
    if status in ("delivered",):
        return ("green", "délivré", when)
    if status == "sent":
        return ("orange", "envoyé", when)
    if status in ("hard_bounce", "soft_bounce", "spam", "failed"):
        return ("red", status.replace("_", " "), when)
    return ("orange", status, when)


def get_all_email_statuses():
    """
    Retourne un dict {user_id: (color, label, sent_at)} pour tous les users
    en une seule requête SQL (évite N+1 dans admin_clients).
    """
    try:
        c = _db().cursor()
        c.execute("""
            SELECT user_id, status, last_event_at, sent_at
            FROM email_logs
            WHERE id IN (SELECT MAX(id) FROM email_logs GROUP BY user_id)
        """)
        out = {}
        for row in c.fetchall():
            uid = row["user_id"]
            status, last_event, sent_at = row["status"], row["last_event_at"], row["sent_at"]
            when = last_event or sent_at
            if status in ("opened", "clicked"):
                out[uid] = ("green", status, when)
            elif status == "delivered":
                out[uid] = ("green", "délivré", when)
            elif status == "sent":
                out[uid] = ("orange", "envoyé", when)
            elif status in ("hard_bounce", "soft_bounce", "spam", "failed"):
                out[uid] = ("red", status.replace("_", " "), when)
            else:
                out[uid] = ("orange", status, when)
        return out
    except Exception:
        return {}
