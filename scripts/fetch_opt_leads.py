#!/usr/bin/env python3
"""Pulls lead data for the opt project from a Google Sheet (two tabs, one per
brand) and writes it in the same shape as import_leads.py's data/leads.json,
so the existing "Лиды" card and funnel render it without any changes.

The sheet is shared as "anyone with the link can view", so this reads it via
the public CSV export endpoint — no Google credentials needed, works the same
from a GitHub Actions runner as from a browser.

Sheet tabs (gid, brand label):
  0         -> Урал-К   (columns: Name, Phone, Email, City, Checkbox, referer,
                          formid, Form name, Sent, requestid, Тип обращения,
                          Комментарий, Номер заказа, Сумма, Отргружен)
  638146547 -> Технорес (columns: Имя, Телефон, Email, Город, Дата обращения,
                          Тип обращения, Комментарий, Номер заказа,
                          Сумма реализации)
A row counts as a lead if any cell in it is non-empty. "Номер заказа" +
the sum column are filled only once the deal closes ("реализация").

Environment: OPT_LEADS_SHEET_ID (defaults to the sheet below), DATA_DIR
(default "data/opt").
"""
import csv
import datetime as dt
import io
import json
import os
import re
import urllib.request

SHEET_ID = os.environ.get("OPT_LEADS_SHEET_ID", "1pVA_Tu1gAO1pGOaSppVCz748vW2ReA8U73zVjCXhkTY")
DATA_DIR = os.environ.get("DATA_DIR", "data/opt").rstrip("/")
OUT = DATA_DIR + "/leads.json"

TABS = [
    {"gid": "0", "brand": "Урал-К",
     "cols": ["name", "phone", "email", "city", "checkbox", "referer", "formid",
              "form_name", "date", "requestid", "format", "comment", "order_no", "sum", "shipped"]},
    {"gid": "638146547", "brand": "Технорес",
     "cols": ["name", "phone", "email", "city", "date", "format", "comment", "order_no", "sum"]},
]


def log(*a):
    print(*a, flush=True)


def fetch_csv(gid):
    url = "https://docs.google.com/spreadsheets/d/%s/export?format=csv&gid=%s" % (SHEET_ID, gid)
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def parse_sum(v):
    v = (v or "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not v:
        return 0.0
    try:
        return float(v)
    except ValueError:
        return 0.0


DATE_FORMATS = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def parse_date(v):
    v = (v or "").strip()
    if not v:
        return None
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def load_tab(tab):
    rows = fetch_csv(tab["gid"])
    if not rows:
        return []
    body = rows[1:]  # заголовок
    events = []
    for r in body:
        r = r + [""] * (len(tab["cols"]) - len(r))
        rec = dict(zip(tab["cols"], r))
        if not any((v or "").strip() for v in rec.values()):
            continue
        events.append({
            "city": (rec.get("city") or "").strip() or None,
            "format_label": (rec.get("format") or "").strip().capitalize() or "Не указано",
            "subbrand": tab["brand"],
            "date": parse_date(rec.get("date")),
            "order_no": (rec.get("order_no") or "").strip() or None,
            "revenue": parse_sum(rec.get("sum")),
            "leads": 1,
        })
    log("  %s: строк %d, лидов %d" % (tab["brand"], len(body), len(events)))
    return events


def aggregate(events):
    def bucket(key):
        agg = {}
        for e in events:
            k = key(e) or "—"
            cur = agg.setdefault(k, {"leads": 0, "revenue": 0.0})
            cur["leads"] += e["leads"]
            cur["revenue"] += e["revenue"]
        return sorted(
            [{"key": k, "leads": v["leads"], "revenue": round(v["revenue"], 2)} for k, v in agg.items()],
            key=lambda r: -r["leads"],
        )

    totals = {
        "leads": sum(e["leads"] for e in events),
        "revenue": round(sum(e["revenue"] for e in events), 2),
        "events_with_revenue": sum(e["leads"] for e in events if e["revenue"] > 0),
    }
    return {
        "totals": totals,
        "by_city": bucket(lambda e: e["city"]),
        "by_subbrand": bucket(lambda e: e["subbrand"]),
        "by_format": bucket(lambda e: e["format_label"]),
    }


def main():
    events = []
    for tab in TABS:
        events.extend(load_tab(tab))
    dates = [e["date"] for e in events if e["date"]]
    period = [min(dates), max(dates)] if dates else None
    agg = aggregate(events)
    out = {
        "meta": {
            "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": "Google Sheets (Урал-К + Технорес), автообновление каждые 3 часа",
            "period": period,
            "brand_filter": None,
            "note": "Данные приходят из таблицы лидов отдела продаж. «Сумма»/«Сумма реализации» заполняется только "
                    "когда сделка закрыта — остальные лиды без неё. Разбивка «По сайту/суб-бренду» здесь — это "
                    "Урал-К против Технорес.",
        },
        **agg,
        "events": sorted(events, key=lambda e: -(e["revenue"] or 0)),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log("Итого: лидов %d, выручка %.2f" % (agg["totals"]["leads"], agg["totals"]["revenue"]))
    log("written:", OUT)


if __name__ == "__main__":
    main()
