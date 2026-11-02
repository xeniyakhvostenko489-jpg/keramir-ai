#!/usr/bin/env python3
"""Imports a 1C "universal report" (leads grouped by division -> brand -> city ->
store -> event) and writes data/leads.json for the dashboard.

The report is a manually exported .xlsx from 1C (Регистр сведений, сгруппированный
по Подразделению, Каналу1, Событию), not an API feed — 1C has no automated export
yet, so this is run by hand whenever a fresh report comes in.

Structure (decoded from the file's cell indentation, not from indent width in the
text): each row's A-column indent level marks its place in the tree —
0 ПРЕДПРИЯТИЕ, 2 division, 4 brand, 6 city, 8 store, 10 event ("Событие": format +
sub-brand, e.g. "Входящий звонок. КераМир Шоп"), 12 CRM lead record, 14 order.
Count and revenue are already pre-aggregated by 1C at every level (columns D and F),
so only the depth-10 "event" rows need to be read — no need to walk individual leads.

The event name's format prefix (before the last ". ") says whether it's a phone
call or a website interaction. It does NOT say whether the lead came from Yandex
Direct, organic search, or a direct visit — 1C does not store traffic source yet
(see TZ: offline-conversions). Don't claim Direct attribution from this file.

Usage: python3 scripts/import_leads.py <path-to-xlsx> [--out data/leads.json] [--brand КераМир]
"""
import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict

try:
    import openpyxl
except ImportError:
    raise SystemExit("openpyxl is required: pip install openpyxl")

KNOWN_SUBBRANDS = [
    "Кафель.Маркет (Ельцов)", "УралКафель (Ельцов)", "Каталог КераМир Шоп",
    "Керамогранит-онлайн", "Кафель-онлайн", "КераМир Шоп",
]


def split_event(name):
    """"Входящий звонок. КераМир Шоп" -> ("Входящий звонок", "КераМир Шоп")."""
    for sb in KNOWN_SUBBRANDS:
        suffix = ". " + sb
        if name.endswith(suffix):
            return name[: -len(suffix)], sb
    return name, None


def classify_format(prefix):
    if prefix.startswith("Входящий звонок"):
        return "call", "Звонок"
    if prefix.startswith("ПРОФ КМ") or prefix.startswith("Почта"):
        return "other", "Другое"
    return "site", "Заявка с сайта"


def parse_period(ws):
    for row in ws.iter_rows(min_row=1, max_row=5, max_col=6):
        for cell in row:
            if isinstance(cell.value, str) and "Период:" in cell.value:
                m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", cell.value)
                if m:
                    f = dt.datetime.strptime(m.group(1), "%d.%m.%Y").date().isoformat()
                    t = dt.datetime.strptime(m.group(2), "%d.%m.%Y").date().isoformat()
                    return [f, t]
    return None


def indent_of(cell):
    return cell.alignment.indent if cell.alignment else None


def parse(path, brand):
    wb = openpyxl.load_workbook(path, read_only=False, data_only=True)
    ws = wb.active
    period = parse_period(ws)

    # find the row range for the requested brand (depth-4 label)
    start = end = None
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=1):
        cell = row[0]
        if cell.value == brand and indent_of(cell) == 4.0:
            start = cell.row
        elif start is not None and end is None and indent_of(cell) == 4.0 and cell.row > start:
            end = cell.row - 1
    if start is None:
        raise SystemExit("brand %r not found in the report (depth-4 label)" % brand)
    if end is None:
        end = ws.max_row

    events = []
    city = store = None
    for r in range(start, end + 1):
        cell = ws.cell(row=r, column=1)
        if cell.value is None or not isinstance(cell.value, str):
            continue
        ind = indent_of(cell)
        if ind == 6.0:
            city = cell.value.lstrip("-_ ").split("_", 1)[-1].lstrip("-_ ")
        elif ind == 8.0:
            store = cell.value
        elif ind == 10.0:
            count = ws.cell(row=r, column=6).value or 0
            revenue = ws.cell(row=r, column=4).value or 0
            prefix, subbrand = split_event(cell.value)
            fmt, fmt_label = classify_format(prefix)
            events.append({
                "city": city, "store": store, "event": cell.value,
                "subbrand": subbrand, "format": fmt, "format_label": fmt_label,
                "leads": int(count), "revenue": round(float(revenue), 2),
            })
    return period, events


def aggregate(events):
    def bucket(key):
        agg = defaultdict(lambda: {"leads": 0, "revenue": 0.0})
        for e in events:
            k = key(e)
            agg[k]["leads"] += e["leads"]
            agg[k]["revenue"] += e["revenue"]
        return sorted(
            [{"key": k, "leads": v["leads"], "revenue": round(v["revenue"], 2)} for k, v in agg.items()],
            key=lambda r: -r["leads"],
        )

    totals_leads = sum(e["leads"] for e in events)
    totals_revenue = round(sum(e["revenue"] for e in events), 2)
    orders_with_revenue = sum(e["leads"] for e in events if e["revenue"] > 0)
    return {
        "totals": {"leads": totals_leads, "revenue": totals_revenue, "events_with_revenue": orders_with_revenue},
        "by_city": bucket(lambda e: e["city"] or "—"),
        "by_subbrand": bucket(lambda e: e["subbrand"] or "без суб-бренда"),
        "by_format": bucket(lambda e: e["format_label"]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--out", default="data/leads.json")
    ap.add_argument("--brand", default="КераМир")
    args = ap.parse_args()

    period, events = parse(args.xlsx, args.brand)
    agg = aggregate(events)
    out = {
        "meta": {
            "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": "1C, Универсальный отчёт (ручная выгрузка, регистр сведений по Подразделению/Каналу1/Событию)",
            "period": period,
            "brand_filter": args.brand,
            "note": "Событие даёт формат обращения (звонок/сайт) и под-бренд сайта, но не источник трафика "
                    "(Директ/органика/прямой заход) — этого поля в 1С пока нет.",
        },
        **agg,
        "events": sorted(events, key=lambda e: -e["leads"]),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("brand:", args.brand, "period:", period)
    print("leads:", agg["totals"]["leads"], "revenue:", agg["totals"]["revenue"])
    print("written:", args.out)


if __name__ == "__main__":
    main()
