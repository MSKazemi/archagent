#!/usr/bin/env python3
"""HTTP healthcheck for ArchAgent containers/systemd checks."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

host = os.getenv("ARCHAGENT_HEALTH_HOST", "127.0.0.1")
port = os.getenv("ARCHAGENT_PORT", "8091")
url = f"http://{host}:{port}/api/health"
try:
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if response.status == 200 and payload.get("ok") is True:
        print("ok")
        raise SystemExit(0)
    print(payload)
except Exception as exc:
    print(f"healthcheck failed: {exc}", file=sys.stderr)
raise SystemExit(1)
