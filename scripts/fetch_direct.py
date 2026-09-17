#!/usr/bin/env python3
"""Fetch a daily campaign report from Yandex Direct (Reports API v5) and write data/direct.json.

Environment:
  DIRECT_TOKEN  - OAuth token of the Direct account (required)
  CLIENT_LOGIN  - client login, only for agency accounts (optional)
  DASH_KEY      - passphrase; when set, the payload is encrypted with AES-GCM (optional)
  DAYS          - how many days back to fetch, default 200

Optional data/config.json: {"goals": ["123456", "234567"]} - Metrika goal IDs counted as leads.
"""
import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

API = "https://api.direct.yandex.com/json/v5/reports"
FIELDS = ["Date", "CampaignId", "CampaignName", "CampaignType", "AdNetworkType", "Device",
          "Impressions", "Clicks", "Cost", "Sessions", "Bounces", "Conversions"]


def log(*a):
    print(*a, flush=True)


def load_config():
    try:
        with open("data/config.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def request_report(token, login, date_from, date_to, goals):
    params = {
        "SelectionCriteria": {"DateFrom": date_from, "DateTo": date_to},
        "FieldNames": FIELDS,
        "ReportName": "km_dash_%s_%s_%s" % (date_from, date_to, hashlib.md5(",".join(goals).encode()).hexdigest()[:8]),
        "ReportType": "CUSTOM_REPORT",
        "DateRangeType": "CUSTOM_DATE",
        "Format": "TSV",
        "IncludeVAT": "YES",
    }
    if goals:
        params["Goals"] = goals
        params["AttributionModels"] = ["AUTO"]
    headers = {
        "Authorization": "Bearer " + token,
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
        "processingMode": "auto",
        "returnMoneyInMicros": "false",
        "skipReportHeader": "true",
        "skipReportSummary": "true",
    }
    if login:
        headers["Client-Login"] = login
    body = json.dumps({"params": params}).encode()
    for _ in range(60):
        req = urllib.request.Request(API, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, hdrs, text = r.status, r.headers, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            status, hdrs, text = e.code, e.headers, e.read().decode("utf-8", "replace")
        if status == 200:
            return text
        if status in (201, 202):
            wait = min(max(int(hdrs.get("retryIn", "5") or 5), 2), 60)
            log("report queued (%s), retry in %ss" % (status, wait))
            time.sleep(wait)
            continue
        try:
            err = json.loads(text).get("error", {})
            msg = " | ".join(str(err.get(k, "")) for k in ("error_code", "error_string", "error_detail"))
        except Exception:
            msg = text[:300]
        raise SystemExit("Direct API error HTTP %s: %s" % (status, msg))
    raise SystemExit("report was not ready in time")


def parse_tsv(text):
    lines = [l for l in text.splitlines() if l]
    if lines and lines[0].startswith('"'):
        lines.pop(0)
    if lines and lines[-1].startswith("Total"):
        lines.pop()
    if not lines:
        return []
    head = lines.pop(0).split("\t")
    idx = {n: i for i, n in enumerate(head)}
    conv_cols = [i for i, h in enumerate(head) if h.startswith("Conversions")]

    def num(v):
        if v in (None, "", "--"):
            return None
        return float(v.replace(",", "."))

    rows = []
    for l in lines:
        c = l.split("\t")
        conv = None
        for i in conv_cols:
            v = num(c[i])
            if v is not None:
                conv = (conv or 0) + v
        rows.append([
            c[idx["Date"]], c[idx["CampaignId"]], c[idx["CampaignName"]], c[idx["CampaignType"]],
            c[idx["AdNetworkType"]], c[idx["Device"]],
            num(c[idx["Impressions"]]) or 0, num(c[idx["Clicks"]]) or 0, num(c[idx["Cost"]]) or 0,
            num(c[idx["Sessions"]]), num(c[idx["Bounces"]]), conv,
        ])
    return rows


def encrypt(payload_bytes, passphrase):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(12)
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 100000, 32)
    ct = AESGCM(key).encrypt(iv, payload_bytes, None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"enc": "aes-gcm-pbkdf2-sha256-100000", "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def main():
    token = os.environ.get("DIRECT_TOKEN", "").strip()
    if not token:
        raise SystemExit("DIRECT_TOKEN is not set (add it in repository Settings -> Secrets and variables -> Actions)")
    login = os.environ.get("CLIENT_LOGIN", "").strip()
    key = os.environ.get("DASH_KEY", "").strip()
    days = int(os.environ.get("DAYS", "200"))
    cfg = load_config()
    goals = [str(g) for g in cfg.get("goals", []) if str(g).isdigit()]

    today = dt.datetime.utcnow().date()
    date_to = today - dt.timedelta(days=1)
    date_from = date_to - dt.timedelta(days=days - 1)
    log("fetching %s .. %s, goals=%s" % (date_from, date_to, goals or "default"))
    rows = parse_tsv(request_report(token, login, date_from.isoformat(), date_to.isoformat(), goals))

    meta = {
        "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "from": date_from.isoformat(), "to": date_to.isoformat(),
        "rows": len(rows),
        "campaigns": len({r[1] for r in rows}),
        "has_sessions": any(r[9] is not None for r in rows),
        "has_conversions": any(r[11] is not None for r in rows),
        "goals": goals,
        "encrypted": bool(key),
    }
    payload = json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    os.makedirs("data", exist_ok=True)
    if key:
        out = encrypt(payload, key)
        out["meta"] = meta
    else:
        out = {"meta": meta, "rows": rows}
    with open("data/direct.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    # Only counts go to the (public) log - never figures.
    log("done:", json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
