#!/usr/bin/env python3
"""Shared helpers for the Yandex Direct API v5: requests, reports, encryption."""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.request

BASE = "https://api.direct.yandex.com/json/v5/"


def log(*a):
    print(*a, flush=True)


def token():
    t = os.environ.get("DIRECT_TOKEN", "").strip()
    if not t:
        raise SystemExit("DIRECT_TOKEN is not set (repository Settings -> Secrets and variables -> Actions)")
    return t


def _headers(extra=None):
    h = {"Authorization": "Bearer " + token(), "Accept-Language": "ru",
         "Content-Type": "application/json; charset=utf-8"}
    login = os.environ.get("CLIENT_LOGIN", "").strip()
    if login:
        h["Client-Login"] = login
    h.update(extra or {})
    return h


def _error(text, status):
    try:
        e = json.loads(text).get("error", {})
        msg = " | ".join(str(e.get(k)) for k in ("error_code", "error_string", "error_detail") if e.get(k))
    except Exception:
        msg = text[:300]
    return "HTTP %s: %s" % (status, msg or "unknown error")


def post(path, body, extra_headers=None, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers=_headers(extra_headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


def call(path, method, params):
    """A regular (non-report) API call. Returns the `result` object.

    Direct answers some failures with HTTP 200 and an `error` object in the body,
    so a missing `result` is an error too, not an empty answer.
    """
    status, _, text = post(path, {"method": method, "params": params})
    if status != 200:
        raise RuntimeError("%s.%s %s" % (path, method, _error(text, status)))
    body = json.loads(text)
    if "result" not in body:
        raise RuntimeError("%s.%s %s" % (path, method, _error(text, status)))
    return body["result"]


def get_all(path, params, key, page_size=10000):
    """A paginated `get` call. Returns the full list under `key`."""
    out, offset = [], 0
    while True:
        p = dict(params)
        p["Page"] = {"Limit": page_size, "Offset": offset}
        res = call(path, "get", p)
        chunk = res.get(key, [])
        out.extend(chunk)
        limited = (res.get("LimitedBy") or 0)
        if not chunk or not limited:
            break
        offset = int(limited)
    return out


def get_by_campaigns(path, base_params, key, campaign_ids, chunk=10):
    """`get` for objects that require a campaign filter, in chunks of campaign ids."""
    out = []
    for i in range(0, len(campaign_ids), chunk):
        p = dict(base_params)
        p["SelectionCriteria"] = dict(p.get("SelectionCriteria") or {},
                                      CampaignIds=[int(c) for c in campaign_ids[i:i + chunk]])
        out.extend(get_all(path, p, key))
    return out


GOALS_PER_REPORT = 10   # жёсткий лимит Директа на массив Goals


def report(name, fields, date_from, date_to, report_type="CUSTOM_REPORT",
           goals=None, criteria=None, include_vat=True):
    """Runs a report and returns (header, rows) with rows as lists of strings.

    Когда заданы цели, Директ возвращает колонку на каждую цель и не отдаёт общую
    Conversions, а в один отчёт помещается не больше десяти целей. Поэтому список
    целей режется на части, а достижения складываются обратно в одну колонку
    Conversions — для вызывающего кода ничего не меняется.
    """
    if goals and len(goals) > GOALS_PER_REPORT:
        return _report_chunked_goals(name, fields, date_from, date_to, report_type,
                                     goals, criteria, include_vat)
    return _report_once(name, fields, date_from, date_to, report_type, goals, criteria, include_vat)


def _report_chunked_goals(name, fields, date_from, date_to, report_type, goals, criteria, include_vat):
    dims = [f for f in fields if f != "Conversions"]
    merged, order = {}, []
    for i in range(0, len(goals), GOALS_PER_REPORT):
        chunk = goals[i:i + GOALS_PER_REPORT]
        head, rows = _report_once("%s_g%d" % (name, i // GOALS_PER_REPORT), fields, date_from,
                                  date_to, report_type, chunk, criteria, include_vat)
        if not rows:
            continue
        ix = {h: j for j, h in enumerate(head)}
        conv_cols = [h for h in head if h.startswith("Conversions")]
        for r in rows:
            key = tuple(r[ix[d]] for d in dims if d in ix)
            row = merged.get(key)
            if row is None:
                row = merged[key] = {d: (r[ix[d]] if d in ix else "") for d in dims}
                row["Conversions"] = 0.0
                order.append(key)
            for c in conv_cols:
                v = r[ix[c]]
                if v not in ("", "--"):
                    row["Conversions"] += float(v)
    out = []
    for key in order:
        row = merged[key]
        out.append([("%g" % row["Conversions"]) if f == "Conversions" else row.get(f, "") for f in fields])
    log("  %s: склеено %d строк из %d частей по целям" % (name, len(out), (len(goals) + 9) // 10))
    return list(fields), out


def _report_once(name, fields, date_from, date_to, report_type="CUSTOM_REPORT",
                 goals=None, criteria=None, include_vat=True):
    params = {
        "SelectionCriteria": {"DateFrom": date_from, "DateTo": date_to},
        "FieldNames": fields,
        "ReportName": "km_%s_%s_%s_%s" % (
            name, date_from, date_to,
            hashlib.md5((",".join(fields) + ",".join(goals or [])).encode()).hexdigest()[:8]),
        "ReportType": report_type,
        "DateRangeType": "CUSTOM_DATE",
        "Format": "TSV",
        "IncludeVAT": "YES" if include_vat else "NO",
    }
    if criteria:
        params["SelectionCriteria"].update(criteria)
    if goals:
        params["Goals"] = goals
        params["AttributionModels"] = ["AUTO"]
    extra = {"processingMode": "auto", "returnMoneyInMicros": "false",
             "skipReportHeader": "true", "skipReportSummary": "true"}
    for _ in range(60):
        status, hdrs, text = post("reports", {"params": params}, extra)
        if status in (201, 202):
            wait = min(max(int(hdrs.get("retryIn", "5") or 5), 2), 30)
            log("  отчёт %s формируется, ждём %s с" % (name, wait))
            time.sleep(wait)
            continue
        break
    if status != 200:
        raise RuntimeError("report %s: %s" % (name, _error(text, status)))
    lines = [l for l in text.splitlines() if l]
    if lines and lines[0].startswith('"'):
        lines.pop(0)
    if lines and lines[-1].startswith("Total"):
        lines.pop()
    if not lines:
        return [], []
    head = lines.pop(0).split("\t")
    return head, [l.split("\t") for l in lines]


def num(v):
    if v is None or v in ("", "--"):
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def n0(v):
    x = num(v)
    return 0.0 if x is None else x


def micros(v):
    """Direct returns money in micro-units outside of reports."""
    return None if v is None else round(float(v) / 1e6, 2)


def encrypt(payload_bytes, passphrase):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(12)
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 100000, 32)
    ct = AESGCM(key).encrypt(iv, payload_bytes, None)
    b = lambda x: base64.b64encode(x).decode()
    return {"enc": "aes-gcm-pbkdf2-sha256-100000", "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def read_data(path):
    """Читает файл, записанный write_data, расшифровывая его при необходимости."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    if "enc" not in d:
        return d
    key = os.environ.get("DASH_KEY", "").strip()
    if not key:
        return None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    b = lambda x: base64.b64decode(d[x])
    k = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), b("salt"), 100000, 32)
    out = json.loads(AESGCM(k).decrypt(b("iv"), b("ct"), None).decode("utf-8"))
    out["meta"] = d.get("meta", out.get("meta"))
    return out


def write_data(path, body, meta):
    """Writes a data file, encrypted when DASH_KEY is set."""
    key = os.environ.get("DASH_KEY", "").strip()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if key:
        out = encrypt(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), key)
        out["meta"] = meta
    else:
        out = dict(body)
        out["meta"] = meta
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log("  записан %s (%.1f КБ)" % (path, os.path.getsize(path) / 1024))
