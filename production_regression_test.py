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


def expect_post_http_error(path, payload, code, content_type="application/json", token=TOKEN):
    """POST to path and assert a specific HTTP error code is returned."""
    headers = {"X-ArchAgent-Token": token} if token else {}
    data = json.dumps(payload).encode("utf-8")
    headers["Content-Type"] = content_type
    req = urllib.request.Request(URL + path, data=data, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == code, f"expected HTTP {code} but got {exc.code} for POST {path}"
        return
    raise AssertionError(f"expected HTTP {code} for POST {path}")


def test_bid_profiles(token=TOKEN):
    # 1. GET /api/bid-profiles — must return items list
    result = fetch("/api/bid-profiles", token=token)
    assert "items" in result, result
    assert isinstance(result["items"], list), result

    initial_count = len(result["items"])

    # 2. POST /api/bid-profiles — create a new profile
    payload = {
        "company_name": "Regression Test SRL",
        "soa_qualifications": [{"category": "OG11", "classification": "IV"}],
        "certifications_held": ["ISO 9001:2015"],
        "ateco_codes": ["43.22"],
        "geographic_regions": ["ITA"],
        "avg_project_value_eur": 500000,
        "notes": "Regression test profile",
    }
    created = fetch("/api/bid-profiles", payload=payload, token=token)
    assert "id" in created, created
    assert isinstance(created["id"], int), created
    created_id = created["id"]

    # 3. GET /api/bid-profiles again — new profile must appear
    result2 = fetch("/api/bid-profiles", token=token)
    assert "items" in result2, result2
    assert len(result2["items"]) == initial_count + 1, result2
    ids = [item["id"] for item in result2["items"]]
    assert created_id in ids, f"created id {created_id} not found in {ids}"

    # 4. POST /api/dossier/analyze with JSON (not multipart) — must return 400
    expect_post_http_error(
        "/api/dossier/analyze",
        {"notice_id": "test"},
        400,
        content_type="application/json",
        token=token,
    )

    return created_id


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

    bad_env = os.environ.copy()
    bad_env["ARCHAGENT_TOKEN"] = "change-me-use-openssl-rand-hex-32"
    bad_token = subprocess.run(
        [sys.executable, "archagent_server.py", "--port", "8099"],
        cwd=BASE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=bad_env,
        timeout=5,
    )
    assert bad_token.returncode != 0, bad_token.stdout + bad_token.stderr
    assert "placeholder ARCHAGENT_TOKEN" in (bad_token.stdout + bad_token.stderr)

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
    created_bid_profile_id = None
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
        expect_http_error("/.env", 404)
        expect_http_error("/archagent_app.sqlite3", 404)
        expect_http_error("/archagent_server.py", 404)

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
        expect_http_error(report["url"], 404)

        csv_report = fetch(f"/api/lead-radar/export?profile_id={profile['id']}&limit=8&format=csv")
        created_export_paths.append(csv_report["export_path"])
        assert csv_report["export_path"].endswith(".csv"), csv_report
        assert "source_notice_id" in csv_report["csv"], csv_report["csv"][:200]

        created_bid_profile_id = test_bid_profiles()

        print("PASS production regression: importer help, auth, customer profiles, lead radar exports, bid profiles")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if created_profile_id or created_export_paths or created_bid_profile_id:
            import sqlite3
            con = sqlite3.connect(BASE / "archagent_app.sqlite3")
            if created_profile_id:
                con.execute("DELETE FROM customer_profiles WHERE id=?", (created_profile_id,))
                con.execute("DELETE FROM lead_radar_exports WHERE profile_id=?", (created_profile_id,))
                con.execute("DELETE FROM activities WHERE payload_json LIKE ? OR message LIKE ?", (f"%{created_profile_id}%", f"%#{created_profile_id}%"))
            if created_bid_profile_id:
                con.execute("DELETE FROM bid_profiles WHERE id=?", (created_bid_profile_id,))
                con.execute("DELETE FROM activities WHERE payload_json LIKE ?", (f'%"id": {created_bid_profile_id}%',))
            con.commit()
            con.close()
            for path in created_export_paths:
                try:
                    Path(path).unlink()
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    main()
