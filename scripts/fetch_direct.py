#!/usr/bin/env python3
"""Fetch the daily campaign report from Yandex Direct and write data/direct.json.

Each row carries conv - achievements of all Metrika goals linked to the counter
("выполненные цели"). Real leads, orders and revenue are expected to come from
1C once that integration exists; this script no longer tries to approximate
them from a curated subset of Metrika goals.

Environment:
  DIRECT_TOKEN  - OAuth token of the Direct account (required)
  CLIENT_LOGIN  - client login, only for agency accounts (optional)
  DASH_KEY      - passphrase; when set, the payload is encrypted with AES-GCM (optional)
  DAYS          - how many days back to fetch, default 200
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direct_api as api
from direct_api import log, num

OUT = "data/direct.json"
FIELDS = ["Date", "CampaignId", "CampaignName", "CampaignType", "AdNetworkType", "Device",
          "Impressions", "Clicks", "Cost", "Sessions", "Bounces", "Conversions"]


def encrypt(payload_bytes, passphrase):
    """Kept as a re-export: ai_review.py imports encrypt from this module."""
    return api.encrypt(payload_bytes, passphrase)


def fix_micros(rows, cost_i, clicks_i):
    """Если сервер проигнорировал returnMoneyInMicros, расход приходит в микрорублях."""
    clicks = sum(r[clicks_i] or 0 for r in rows)
    cost = sum(r[cost_i] or 0 for r in rows)
    if clicks > 0 and cost / clicks > 50000:
        log("  расход пришёл в микрорублях, делим на миллион")
        for r in rows:
            r[cost_i] = (r[cost_i] or 0) / 1e6


def main():
    api.token()
    days = int(os.environ.get("DAYS", "200"))
    date_to = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    date_from = date_to - dt.timedelta(days=days - 1)
    log("Период %s .. %s" % (date_from, date_to))

    log("Отчёт по всем целям")
    head, raw = api.report("km_all", FIELDS, date_from.isoformat(), date_to.isoformat())
    ix = {h: i for i, h in enumerate(head)}
    rows = []
    for r in raw:
        rows.append([
            r[ix["Date"]], r[ix["CampaignId"]], r[ix["CampaignName"]], r[ix["CampaignType"]],
            r[ix["AdNetworkType"]], r[ix["Device"]],
            num(r[ix["Impressions"]]) or 0, num(r[ix["Clicks"]]) or 0, num(r[ix["Cost"]]) or 0,
            num(r[ix["Sessions"]]), num(r[ix["Bounces"]]), num(r[ix["Conversions"]]),
        ])
    fix_micros(rows, 8, 7)

    meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                          .isoformat().replace("+00:00", "Z"),
        "from": date_from.isoformat(), "to": date_to.isoformat(),
        "rows": len(rows),
        "campaigns": len({r[1] for r in rows}),
        "has_sessions": any(r[9] is not None for r in rows),
        "has_conversions": any(r[11] is not None for r in rows),
        "encrypted": bool(os.environ.get("DASH_KEY", "").strip()),
    }
    api.write_data(OUT, {"rows": rows}, meta)
    log("Готово:", json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
