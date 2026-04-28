#!/usr/bin/env python3
"""
Daily database backup — run at 3am via cron:
  0 3 * * * /opt/vpn-billing/venv/bin/python /opt/vpn-billing/scripts/cron_backup.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring

ok, filename, size = monitoring.run_backup()
status = "OK" if ok else "ERREUR"
print(f"[backup] {status} — {filename} ({size} bytes)")
