"""
brevo.py — Wrapper API Brevo + intégration SQLite.

Endpoints utilisés :
  POST /v3/smtp/email                         → envoi transactionnel
  GET  /v3/account                           → crédits + plan
  GET  /v3/smtp/statistics/aggregatedReport  → KPIs globaux
  GET  /v3/smtp/statistics/events            → events par email
"""
import os
import sqlite3
import logging
import json
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta

DB = "/opt/vpn-billing/vpn_billing.db"
BREVO_BASE = "https://api.brevo.com/v3"
TIMEOUT = 5   # timeout réseau par appel (secondes)

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
    return {"api-key": key, "Accept": "application/json", "Content-Type": "application/json"}


def _get(path, params=None):
    """GET Brevo API — retourne dict JSON ou None si pas de clé."""
    h = _headers()
    if not h:
        return None
    url = f"{BREVO_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")


def _post(path, body):
    """POST Brevo API avec corps JSON — retourne dict JSON ou lève Exception."""
    h = _headers()
    if not h:
        raise Exception("Clé API Brevo non configurée")
    url = f"{BREVO_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")


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
    if not get_api_key():
        return None
    try:
        d = _get("/account")
        if d is None:
            return None
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
    if not get_api_key():
        return None
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        d = _get("/smtp/statistics/aggregatedReport", params={"startDate": start, "endDate": end})
        return d
    except Exception as e:
        _log_error(f"get_aggregated_stats: {e}", "brevo.py:get_aggregated_stats")
        return None


def get_events_for_email(email, days=90, limit=50):
    """Retourne la liste des événements Brevo pour un destinataire."""
    if not get_api_key():
        return []
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")
    try:
        d = _get("/smtp/statistics/events",
                 params={"email": email, "startDate": start, "endDate": end, "limit": limit})
        return (d or {}).get("events", [])
    except Exception as e:
        _log_error(f"get_events_for_email({email}): {e}", "brevo.py:get_events_for_email")
        return []


# ─── Envoi transactionnel ─────────────────────────────────────────────────────

def send_transactional(to_email, subject, html_content, sender_email=None, sender_name="VPN Privé"):
    """
    Envoie un email via l'API Brevo (POST /v3/smtp/email).
    Retourne (True, message_id) ou (False, message_erreur).
    sender_email doit être une adresse vérifiée dans le compte Brevo.
    """
    if not get_api_key():
        return False, "Clé API Brevo non configurée"

    # Récupère l'email expéditeur depuis settings si non fourni
    if not sender_email:
        try:
            c = _db().cursor()
            c.execute("SELECT value FROM settings WHERE key='smtp_email'")
            row = c.fetchone()
            sender_email = row[0].strip() if row and row[0] else None
        except Exception:
            pass
    if not sender_email:
        return False, "Email expéditeur non configuré (paramètre smtp_email)"

    body = {
        "sender":      {"name": sender_name, "email": sender_email},
        "to":          [{"email": to_email}],
        "subject":     subject,
        "htmlContent": html_content,
    }
    try:
        result = _post("/smtp/email", body)
        msg_id = (result or {}).get("messageId")
        return True, msg_id
    except Exception as e:
        _log_error(f"send_transactional({to_email}): {e}", "brevo.py:send_transactional")
        return False, str(e)


_KPIS_ZERO = {
    "api_ok": False, "credits_restants": 0,
    "envoyes_30j": 0, "delivres_30j": 0,
    "taux_ouverture": 0, "taux_bounce": 0,
    "hard_bounces": 0, "soft_bounces": 0,
}


def get_kpis():
    """
    KPIs synthétiques — toujours retourne un dict complet.
    Les 2 appels Brevo sont exécutés en parallèle ; timeout total = TIMEOUT+1 s.
    """
    if not get_api_key():
        return dict(_KPIS_ZERO)

    # Appels parallèles pour ne pas cumuler les timeouts
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_stats   = ex.submit(get_aggregated_stats, 30)
        f_account = ex.submit(get_account)
        try:
            stats = f_stats.result(timeout=TIMEOUT + 1) or {}
        except Exception:
            stats = {}
        try:
            account = f_account.result(timeout=TIMEOUT + 1) or {}
        except Exception:
            account = {}

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
