#!/usr/bin/env python3
"""
vpn_metrics.py — Collecte les métriques de transfer par peer toutes les 60s.

Lance via systemd timer : setup_metrics_timer.sh
Stocke dans peer_metrics (SQLite). Purge auto à 7 jours.
"""

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

DB_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vpn_billing.db")
CONTAINER = "amnezia-awg"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peer_metrics (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_ip    TEXT    NOT NULL,
            ts         TEXT    NOT NULL,
            rx_bytes   INTEGER NOT NULL DEFAULT 0,
            tx_bytes   INTEGER NOT NULL DEFAULT 0,
            handshake_age_s INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pm_ip_ts ON peer_metrics(peer_ip, ts)")
    conn.commit()


def awg_show():
    """Retourne la sortie de 'awg show' ou 'wg show' depuis le container."""
    for cmd in (["awg", "show"], ["wg", "show"]):
        r = subprocess.run(
            ["docker", "exec", CONTAINER] + cmd,
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            return r.stdout
    return ""


def parse_size(s):
    m = re.match(r'([\d.]+)\s*(B|KiB|MiB|GiB)', s.strip())
    if not m:
        return 0
    v    = float(m.group(1))
    mult = {'B': 1, 'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3}
    return int(v * mult.get(m.group(2), 1))


def parse_hs_age(hs_str):
    total = 0
    for val, unit in re.findall(r'(\d+)\s+(second|minute|hour|day|week)s?', hs_str):
        val = int(val)
        if   'second' in unit: total += val
        elif 'minute' in unit: total += val * 60
        elif 'hour'   in unit: total += val * 3600
        elif 'day'    in unit: total += val * 86400
        elif 'week'   in unit: total += val * 604800
    return total if total else None


def parse_peers(output):
    """Parse awg show output → list of {ip, rx, tx, handshake_age_s}"""
    peers = []
    cur = {}
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("peer:"):
            if cur.get("ip"):
                peers.append(cur)
            cur = {}
        elif s.startswith("allowed ips:"):
            cur["ip"] = s.split(":", 1)[1].strip().split(",")[0].split("/")[0].strip()
        elif s.startswith("transfer:"):
            seg = s.split(":", 1)[1]
            for part in seg.split(","):
                part = part.strip()
                if "received" in part:
                    cur["rx"] = parse_size(part.replace("received", "").strip())
                elif "sent" in part:
                    cur["tx"] = parse_size(part.replace("sent", "").strip())
        elif s.startswith("latest handshake:"):
            cur["handshake_age_s"] = parse_hs_age(s.split(":", 1)[1].strip())
    if cur.get("ip"):
        peers.append(cur)
    return peers


def purge_old(conn):
    cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM peer_metrics WHERE ts < ?", (cutoff,))
    conn.commit()


def collect():
    output = awg_show()
    if not output:
        print(f"[vpn_metrics] awg/wg show failed — container down?", flush=True)
        sys.exit(1)

    peers = parse_peers(output)
    if not peers:
        print(f"[vpn_metrics] Aucun peer trouvé.", flush=True)
        return

    conn = get_db()
    ensure_table(conn)
    purge_old(conn)

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for p in peers:
        conn.execute(
            "INSERT INTO peer_metrics (peer_ip, ts, rx_bytes, tx_bytes, handshake_age_s) "
            "VALUES (?, ?, ?, ?, ?)",
            (p["ip"], ts, p.get("rx", 0), p.get("tx", 0), p.get("handshake_age_s"))
        )
    conn.commit()
    conn.close()
    print(f"[vpn_metrics] {ts} — {len(peers)} peer(s) enregistrés.", flush=True)


if __name__ == "__main__":
    collect()
