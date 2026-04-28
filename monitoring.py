"""
monitoring.py — VPS health, bandwidth, backups, error logs.
"""
import os
import sqlite3
import subprocess
import json
import time
import shutil
import logging
from datetime import datetime, timedelta

DB = "/opt/vpn-billing/vpn_billing.db"
BACKUP_DIR = "/opt/vpn-billing/backups"
TIMEOUT = 5

log = logging.getLogger("monitoring")

AWG_CONTAINERS = [
    ("amnezia-awg",  "awg0"),
    ("amnezia-awg2", "awg0"),
]


# ─── DB helper ────────────────────────────────────────────────────────────────

def _db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _log_error(message, source="monitoring", file_line=""):
    try:
        c = _db()
        c.execute(
            "INSERT INTO error_logs (level, source, message, file_line) VALUES ('error', ?, ?, ?)",
            (source, str(message)[:500], file_line),
        )
        c.commit()
        c.close()
    except Exception:
        pass


# ─── VPS Health ───────────────────────────────────────────────────────────────

def get_cpu_percent():
    """CPU usage via two /proc/stat reads 200ms apart."""
    def _read():
        with open("/proc/stat") as f:
            line = f.readline()
        vals = list(map(int, line.split()[1:]))
        idle = vals[3]
        total = sum(vals)
        return idle, total

    try:
        i1, t1 = _read()
        time.sleep(0.2)
        i2, t2 = _read()
        dt = t2 - t1
        if dt == 0:
            return 0.0
        return round((1 - (i2 - i1) / dt) * 100, 1)
    except Exception:
        return 0.0


def get_ram():
    """Returns dict: total_mb, used_mb, percent."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k.strip()] = int(v.strip().split()[0])
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used  = total - avail
        return {
            "total_mb": round(total / 1024),
            "used_mb":  round(used  / 1024),
            "percent":  round(used / total * 100, 1) if total else 0,
        }
    except Exception:
        return {"total_mb": 0, "used_mb": 0, "percent": 0}


def get_disk():
    """Returns dict: total_gb, used_gb, percent for /."""
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free  = st.f_bavail * st.f_frsize
        used  = total - free
        return {
            "total_gb": round(total / 1024**3, 1),
            "used_gb":  round(used  / 1024**3, 1),
            "percent":  round(used / total * 100, 1) if total else 0,
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "percent": 0}


def get_load():
    """Returns (load1, load5, load15) from /proc/loadavg."""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        return 0.0, 0.0, 0.0


def get_uptime():
    """Returns uptime string like '12j 3h 45m'."""
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
        days    = int(seconds // 86400)
        hours   = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        parts = []
        if days:
            parts.append(f"{days}j")
        if hours:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)
    except Exception:
        return "?"


def get_public_ip():
    """Returns public IP string or '?' on failure."""
    try:
        out = subprocess.check_output(
            ["curl", "-s", "--max-time", "3", "https://ifconfig.me"],
            timeout=5,
        )
        return out.decode().strip()
    except Exception:
        return "?"


def get_ssl_cert_days_remaining(domain=None):
    """Returns int days remaining for SSL cert, or None on error."""
    if not domain:
        try:
            c = _db().cursor()
            c.execute("SELECT value FROM settings WHERE key='domain'")
            row = c.fetchone()
            domain = row[0].strip() if row and row[0] else None
        except Exception:
            pass
    if not domain:
        return None
    try:
        out = subprocess.check_output(
            ["openssl", "s_client", "-connect", f"{domain}:443", "-servername", domain],
            input=b"",
            stderr=subprocess.STDOUT,
            timeout=TIMEOUT,
        )
        cert_out = subprocess.check_output(
            ["openssl", "x509", "-noout", "-enddate"],
            input=out,
            timeout=TIMEOUT,
        )
        date_str = cert_out.decode().split("=", 1)[1].strip()
        expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
        return (expiry - datetime.utcnow()).days
    except Exception:
        return None


# ─── Bandwidth ────────────────────────────────────────────────────────────────

def snapshot_bandwidth():
    """
    Reads AWG dump from all containers and inserts rows into bandwidth_snapshots.
    Called every 5 min by cron_snapshot.py.
    """
    inserted = 0
    for container, iface in AWG_CONTAINERS:
        try:
            out = subprocess.check_output(
                ["docker", "exec", container, "awg", "show", iface, "dump"],
                timeout=TIMEOUT,
                stderr=subprocess.DEVNULL,
            )
            lines = out.decode().strip().split("\n")
            if len(lines) < 2:
                continue
            c = _db()
            for line in lines[1:]:
                parts = line.split("\t")
                if len(parts) < 5:
                    continue
                public_key     = parts[0]
                rx_bytes       = int(parts[2]) if parts[2].isdigit() else 0
                tx_bytes       = int(parts[3]) if parts[3].isdigit() else 0
                last_handshake = int(parts[4]) if parts[4].isdigit() else 0
                c.execute(
                    "INSERT INTO bandwidth_snapshots (iface, public_key, rx_bytes, tx_bytes, last_handshake) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (iface, public_key, rx_bytes, tx_bytes, last_handshake),
                )
                inserted += 1
            c.commit()
            c.close()
        except Exception as e:
            _log_error(f"snapshot_bandwidth({container}): {e}", file_line="monitoring.py:snapshot_bandwidth")
    return inserted


def get_bandwidth_live():
    """
    Returns dict {iface: {rx_mbps, tx_mbps}} computed from the last 2 snapshots per peer.
    Aggregated across all peers for the iface.
    """
    result = {}
    try:
        c = _db().cursor()
        for _, iface in AWG_CONTAINERS:
            c.execute("""
                SELECT public_key, rx_bytes, tx_bytes, snapshot_at
                FROM bandwidth_snapshots
                WHERE iface=?
                  AND snapshot_at >= datetime('now', '-15 minutes')
                ORDER BY public_key, snapshot_at DESC
            """, (iface,))
            rows = c.fetchall()
            seen = {}
            for row in rows:
                pk = row["public_key"]
                if pk not in seen:
                    seen[pk] = row
                elif "prev" not in seen[pk]:
                    seen[pk] = dict(seen[pk])
                    seen[pk]["prev"] = row

            total_rx = total_tx = 0.0
            for pk, data in seen.items():
                if not isinstance(data, dict) or "prev" not in data:
                    continue
                try:
                    t1 = datetime.fromisoformat(data["prev"]["snapshot_at"])
                    t2 = datetime.fromisoformat(data["snapshot_at"])
                    dt = (t2 - t1).total_seconds()
                    if dt <= 0:
                        continue
                    drx = (data["rx_bytes"] - data["prev"]["rx_bytes"]) / dt / 1024 / 1024
                    dtx = (data["tx_bytes"] - data["prev"]["tx_bytes"]) / dt / 1024 / 1024
                    total_rx += max(drx, 0)
                    total_tx += max(dtx, 0)
                except Exception:
                    pass
            result[iface] = {
                "rx_mbps": round(total_rx, 3),
                "tx_mbps": round(total_tx, 3),
            }
    except Exception as e:
        _log_error(f"get_bandwidth_live: {e}", file_line="monitoring.py:get_bandwidth_live")
    return result


def get_bandwidth_sparkline(iface="awg0", points=12):
    """
    Returns list of `points` total-bytes values for mini sparkline chart.
    One value per snapshot window (most recent `points` windows).
    """
    try:
        c = _db().cursor()
        c.execute("""
            SELECT SUM(rx_bytes + tx_bytes) as total, snapshot_at
            FROM bandwidth_snapshots
            WHERE iface=?
            GROUP BY strftime('%Y-%m-%d %H:%M', snapshot_at)
            ORDER BY snapshot_at DESC
            LIMIT ?
        """, (iface, points))
        rows = c.fetchall()
        values = [row["total"] or 0 for row in rows]
        values.reverse()
        while len(values) < points:
            values.insert(0, 0)
        return values
    except Exception:
        return [0] * points


# ─── Telegram ─────────────────────────────────────────────────────────────────

def get_telegram_status():
    """
    Returns dict: {ok, bot_name, users_with_telegram}.
    Reads bot token from settings table key 'telegram_bot_token'.
    """
    result = {"ok": False, "bot_name": None, "users_with_telegram": 0}
    try:
        c = _db().cursor()
        c.execute("SELECT value FROM settings WHERE key='telegram_bot_token'")
        row = c.fetchone()
        token = row[0].strip() if row and row[0] else ""

        c.execute("SELECT COUNT(*) FROM users WHERE telegram IS NOT NULL AND telegram != ''")
        result["users_with_telegram"] = c.fetchone()[0]

        if not token:
            return result

        import urllib.request
        url = f"https://api.telegram.org/bot{token}/getMe"
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read())
        if data.get("ok"):
            result["ok"] = True
            result["bot_name"] = data["result"].get("username")
    except Exception as e:
        _log_error(f"get_telegram_status: {e}", file_line="monitoring.py:get_telegram_status")
    return result


# ─── Backups ──────────────────────────────────────────────────────────────────

def run_backup():
    """
    Creates a SQLite backup copy to BACKUP_DIR.
    Logs result in backups table. Prunes backups older than 30 days.
    Returns (ok, filename, size_bytes).
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"vpn_billing_{ts}.db"
    dest = os.path.join(BACKUP_DIR, filename)
    status = "ok"
    error_msg = None
    size = 0
    try:
        src = sqlite3.connect(DB)
        dst = sqlite3.connect(dest)
        src.backup(dst)
        src.close()
        dst.close()
        size = os.path.getsize(dest)
        # Prune old backups
        cutoff = datetime.now() - timedelta(days=30)
        for f in os.listdir(BACKUP_DIR):
            if not f.startswith("vpn_billing_") or not f.endswith(".db"):
                continue
            fp = os.path.join(BACKUP_DIR, f)
            if datetime.fromtimestamp(os.path.getmtime(fp)) < cutoff:
                os.remove(fp)
    except Exception as e:
        status = "error"
        error_msg = str(e)[:500]
        _log_error(f"run_backup: {e}", file_line="monitoring.py:run_backup")

    try:
        c = _db()
        c.execute(
            "INSERT INTO backups (filename, size_bytes, status, error_message) VALUES (?, ?, ?, ?)",
            (filename, size, status, error_msg),
        )
        c.commit()
        c.close()
    except Exception:
        pass

    return status == "ok", filename, size


def get_backup_status(limit=5):
    """Returns list of recent backup rows."""
    try:
        c = _db().cursor()
        c.execute("SELECT filename, size_bytes, status, error_message, created_at FROM backups ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]
    except Exception:
        return []


# ─── Error logs ───────────────────────────────────────────────────────────────

def get_recent_errors(limit=20):
    """Returns list of recent error_logs rows."""
    try:
        c = _db().cursor()
        c.execute(
            "SELECT level, source, message, file_line, occurred_at "
            "FROM error_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in c.fetchall()]
    except Exception:
        return []


def count_errors(hours=24):
    """Returns count of errors in the last N hours."""
    try:
        c = _db().cursor()
        c.execute(
            "SELECT COUNT(*) FROM error_logs WHERE occurred_at >= datetime('now', ?)",
            (f"-{hours} hours",),
        )
        return c.fetchone()[0]
    except Exception:
        return 0


# ─── Aggregated health snapshot ───────────────────────────────────────────────

def get_health():
    """Returns a single dict with all health metrics for /admin/monitoring."""
    cpu    = get_cpu_percent()
    ram    = get_ram()
    disk   = get_disk()
    load1, load5, load15 = get_load()
    uptime = get_uptime()

    bw_live = get_bandwidth_live()
    bw_awg0 = bw_live.get("awg0", {"rx_mbps": 0, "tx_mbps": 0})

    sparkline = get_bandwidth_sparkline("awg0", 12)

    tg = get_telegram_status()

    backups = get_backup_status(5)
    last_backup = backups[0] if backups else None

    errors_24h = count_errors(24)
    recent_errors = get_recent_errors(10)

    return {
        "cpu_percent":   cpu,
        "ram":           ram,
        "disk":          disk,
        "load1":         load1,
        "load5":         load5,
        "load15":        load15,
        "uptime":        uptime,
        "bw_rx_mbps":    bw_awg0["rx_mbps"],
        "bw_tx_mbps":    bw_awg0["tx_mbps"],
        "bw_sparkline":  sparkline,
        "telegram":      tg,
        "last_backup":   last_backup,
        "errors_24h":    errors_24h,
        "recent_errors": recent_errors,
    }
