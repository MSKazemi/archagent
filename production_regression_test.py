#!/usr/bin/env python3
"""Regression tests for production-hardening features."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
PORT = 8093
URL = f"http://127.0.0.1:{PORT}"
TOKEN = "test-secret-token"


def fetch(path, payload=None, token=TOKEN):
    headers = {"X-ArchAgent-Token": token} if token else {}
    if payload is None:
        req = urllib.request.Request(URL + path, headers=headers)
    else:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(URL + path, data=data, headers=headers, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=10))


def expect_http_error(path, code):
    try:
        urllib.request.urlopen(URL + path, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == code, exc.code
        return
    raise AssertionError(f"expected HTTP {code} for {path}")


def main():
    help_result = subprocess.run(
        [sys.executable, "expert_worker_importer.py", "--help"],
        cwd=BASE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "--dry-run" in help_result.stdout, help_result.stdout

    area_result = subprocess.run(
        [sys.executable, "expert_worker_importer.py", "--list-areas"],
        cwd=BASE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    assert area_result.returncode == 0, area_result.stderr
    for city in ("Rome", "Milan", "Turin", "Naples", "Bologna"):
        assert city in area_result.stdout, area_result.stdout

    env = os.environ.copy()
    env["ARCHAGENT_TOKEN"] = TOKEN
    proc = subprocess.Popen(
        [sys.executable, "archagent_server.py", "--port", str(PORT)],
        cwd=BASE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    created_profile_id = None
    created_export_paths = []
    try:
        for _ in range(40):
            try:
                stats = fetch("/api/stats")
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("server did not start")

        assert stats["total_leads"] >= 400, stats
        expect_http_error("/api/stats", 401)

        profile = fetch("/api/customer-profiles", {
            "name": "Regression Pilot",
            "company": "ArchAgent Test",
            "email": "pilot@example.com",
            "countries": "DEU,FRA",
            "categories": "painting / finishing,renovation / rehabilitation",
            "trades": "painting, renovation",
            "min_value": 0,
            "max_leads": 12,
            "status": "pilot"
        })
        created_profile_id = profile["id"]
        assert profile["id"], profile
        profiles = fetch("/api/customer-profiles")
        assert any(p["id"] == profile["id"] for p in profiles["items"]), profiles

        report = fetch(f"/api/lead-radar/export?profile_id={profile['id']}&limit=8&format=markdown")
        created_export_paths.append(report["export_path"])
        assert report["export_path"].endswith(".md"), report
        assert "Lead Radar Report" in report["markdown"], report["markdown"][:200]
        assert len(report["items"]) <= 8, len(report["items"])

        csv_report = fetch(f"/api/lead-radar/export?profile_id={profile['id']}&limit=8&format=csv")
        created_export_paths.append(csv_report["export_path"])
        assert csv_report["export_path"].endswith(".csv"), csv_report
        assert "source_notice_id" in csv_report["csv"], csv_report["csv"][:200]

        print("PASS production regression: importer help, auth, customer profiles, lead radar exports")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if created_profile_id or created_export_paths:
            import sqlite3
            con = sqlite3.connect(BASE / "archagent_app.sqlite3")
            if created_profile_id:
                con.execute("DELETE FROM customer_profiles WHERE id=?", (created_profile_id,))
                con.execute("DELETE FROM lead_radar_exports WHERE profile_id=?", (created_profile_id,))
                con.execute("DELETE FROM activities WHERE payload_json LIKE ? OR message LIKE ?", (f"%{created_profile_id}%", f"%#{created_profile_id}%"))
            con.commit()
            con.close()
            for path in created_export_paths:
                try:
                    Path(path).unlink()
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    main()
