#!/usr/bin/env python3
"""
Bandwidth snapshot — run every 5 minutes via cron:
  */5 * * * * /opt/vpn-billing/venv/bin/python /opt/vpn-billing/scripts/cron_snapshot.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring

n = monitoring.snapshot_bandwidth()
print(f"[snapshot] {n} peers enregistrés")
