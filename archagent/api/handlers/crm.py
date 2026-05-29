"""Handler functions for CRM-related API routes (prospects, profiles, follow-ups)."""
from __future__ import annotations

import json

from archagent.core.audit import log_activity
from archagent.core.db import app_conn, now
from archagent.generation.proposals import generate_crm_followup
from archagent.api.handlers.leads import get_lead


def split_csv(value: str) -> list[str]:
    return [x.strip() for x in (value or '').replace(';', ',').split(',') if x.strip()]


def create_prospect(payload: dict, principal=None) -> dict:
    con = app_conn()
    sql = "INSERT INTO prospects(name,email,company,country,role,need,offer,status,value_estimate,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    cur = con.execute(sql, (
        payload.get('name') or payload.get('company') or 'Unnamed prospect',
        payload.get('email', ''),
        payload.get('company', ''),
        payload.get('country', ''),
        payload.get('role', ''),
        payload.get('need', ''),
        payload.get('offer', 'Lead Radar'),
        payload.get('status', 'new'),
        payload.get('value_estimate'),
        now(), now(),
    ))
    pid = cur.lastrowid
    log_activity(con, 'prospect', f'New prospect #{pid}', payload, principal)
    con.commit()
    row = con.execute('SELECT * FROM prospects WHERE id=?', (pid,)).fetchone()
    con.close()
    return dict(row)


def create_customer_profile(payload: dict, principal=None) -> dict:
    con = app_conn()
    sql = "INSERT INTO customer_profiles(name,company,email,countries,categories,trades,min_value,max_leads,status,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    cur = con.execute(sql, (
        payload.get('name') or payload.get('company') or 'Unnamed customer profile',
        payload.get('company', ''),
        payload.get('email', ''),
        payload.get('countries', ''),
        payload.get('categories', ''),
        payload.get('trades', ''),
        payload.get('min_value') if payload.get('min_value') not in ('', None) else None,
        int(payload.get('max_leads') or 30),
        payload.get('status', 'pilot'),
        payload.get('notes', ''),
        now(), now(),
    ))
    pid = cur.lastrowid
    log_activity(con, 'customer_profile', f'Created customer profile #{pid}', payload, principal)
    con.commit()
    row = con.execute('SELECT * FROM customer_profiles WHERE id=?', (pid,)).fetchone()
    con.close()
    return dict(row)


def get_customer_profile(profile_id) -> dict | None:
    if not profile_id:
        return None
    con = app_conn()
    row = con.execute('SELECT * FROM customer_profiles WHERE id=?', (profile_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def create_followup(payload: dict, principal=None) -> dict:
    prospect_id = payload.get('prospect_id')
    if not prospect_id:
        raise ValueError('prospect_id is required')
    con = app_conn()
    prospect = con.execute('SELECT * FROM prospects WHERE id=?', (prospect_id,)).fetchone()
    if not prospect:
        con.close()
        raise ValueError('prospect not found')
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id') or None
    lead = get_lead(notice_id) if notice_id else None
    pack = generate_crm_followup(dict(prospect), lead)
    cur = con.execute(
        'INSERT INTO followups(prospect_id,lead_notice_id,subject,body,call_script,tasks_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (prospect_id, notice_id, pack['subject'], pack['email'], pack['call_script'], json.dumps(pack['tasks'], ensure_ascii=False), payload.get('status', 'draft'), now(), now()),
    )
    fid = cur.lastrowid
    log_activity(con, 'followup', f'Created follow-up #{fid} for prospect #{prospect_id}',
                 {'prospect_id': prospect_id, 'notice_id': notice_id}, principal)
    con.commit()
    row = con.execute('SELECT * FROM followups WHERE id=?', (fid,)).fetchone()
    con.close()
    result = dict(row)
    result.update(pack)
    result['id'] = fid
    return result


def profile_to_lead_params(profile, params: dict) -> dict:
    lead_params = {k: v for k, v in params.items()}
    if profile:
        countries = split_csv(profile.get('countries'))
        categories = split_csv(profile.get('categories'))
        if countries and not lead_params.get('country'):
            lead_params['country'] = [countries[0]]
        if categories and not lead_params.get('category'):
            lead_params['category'] = [categories[0]]
        if profile.get('min_value') not in ('', None) and not lead_params.get('min_value'):
            lead_params['min_value'] = [str(profile.get('min_value'))]
    return lead_params
