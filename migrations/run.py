#!/usr/bin/env python3
"""Run all .sql migration files in order (idempotent)."""
import sqlite3, os, sys

DB = os.environ.get("VPN_DB", "/opt/vpn-billing/vpn_billing.db")
mig_dir = os.path.dirname(os.path.abspath(__file__))

conn = sqlite3.connect(DB)
for f in sorted(os.listdir(mig_dir)):
    if f.endswith(".sql"):
        path = os.path.join(mig_dir, f)
        with open(path) as fh:
            conn.executescript(fh.read())
        print(f"[ok] {f}")
conn.commit()
conn.close()
print("Done.")
