"""Handler functions for pilot request API routes."""
from __future__ import annotations

import email.mime.multipart
import email.mime.text
import json
import os
import smtplib
import sys
import threading

from archagent.core.db import app_conn, now


def _send_pilot_notification(data: dict) -> None:
    """Send email notification for a new pilot request. Silently skips if SMTP not configured."""
    host = os.environ.get('SMTP_HOST', '').strip()
    port = int(os.environ.get('SMTP_PORT', '587'))
    user = os.environ.get('SMTP_USER', '').strip()
    password = os.environ.get('SMTP_PASS', '').strip()
    from_addr = os.environ.get('SMTP_FROM', user)
    to_addr = os.environ.get('NOTIFY_EMAIL', user)
    if not host or not user or not to_addr:
        return
    try:
        msg = email.mime.multipart.MIMEMultipart()
        msg['From'] = from_addr
        msg['To'] = to_addr
        msg['Subject'] = f"[ArchAgent] Pilot request — {data.get('name', '')} ({data.get('company', '')})"
        body = (
            f"New pilot request:\n\n"
            f"Name:     {data.get('name', '')}\n"
            f"Email:    {data.get('email', '')}\n"
            f"Company:  {data.get('company', '')}\n"
            f"Country:  {data.get('country', '')}\n"
            f"Service:  {data.get('service', '')}\n"
            f"Deadline: {data.get('tender_deadline', '')}\n\n"
            f"Brief:\n{data.get('brief', '')}\n"
        )
        msg.attach(email.mime.text.MIMEText(body, 'plain'))
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as exc:
        print(f'[WARN] pilot notification email failed: {exc}', file=sys.stderr, flush=True)


def create_pilot_request(payload: dict) -> dict:
    name = (payload.get('name') or '').strip()
    email_addr = (payload.get('email') or '').strip()
    if not name or not email_addr:
        raise ValueError('name and email are required')
    con = app_conn()
    cur = con.execute(
        'INSERT INTO pilot_requests(name,email,company,country,service,tender_deadline,brief,created_at) VALUES (?,?,?,?,?,?,?,?)',
        (name, email_addr, payload.get('company', ''), payload.get('country', ''),
         payload.get('service', ''), payload.get('tender_deadline', ''), payload.get('brief', ''), now()),
    )
    rid = cur.lastrowid
    con.execute(
        'INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)',
        ('pilot_request', f'Pilot request from {name} <{email_addr}>', json.dumps(payload), now()),
    )
    con.commit()
    con.close()
    threading.Thread(target=_send_pilot_notification, args=(payload,), daemon=True).start()
    return {'id': rid, 'status': 'received'}
