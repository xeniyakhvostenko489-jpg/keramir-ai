#!/usr/bin/env python3
"""One-off diagnostic: list campaigns visible to DIRECT_TOKEN, to check whether
a freshly-approved account actually has any campaigns yet. Not part of the
regular dashboard pipeline. Writes <DATA_DIR>/campaigns_dump.json."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direct_api as api
from direct_api import log

DATA_DIR = os.environ.get("DATA_DIR", "data").rstrip("/")
OUT = DATA_DIR + "/campaigns_dump.json"


def main():
    api.token()
    result = api.call("campaigns", "get", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "Type", "Status", "State"],
    })
    campaigns = result.get("Campaigns", [])
    log("Кампаний всего: %d" % len(campaigns))
    for c in campaigns:
        log("  id=%s  type=%s  status=%s/%s  name=%r" % (
            c.get("Id"), c.get("Type"), c.get("Status"), c.get("State"), c.get("Name")))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"campaigns": campaigns}, f, ensure_ascii=False, indent=1)
    log("written:", OUT)

    import datetime as dt
    date_to = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    date_from = date_to - dt.timedelta(days=29)

    log("\n--- тест отчёта: базовые поля, без Sessions/Bounces/Conversions ---")
    try:
        head, rows = api.report("diag_basic", ["Date", "CampaignId", "CampaignName", "Impressions", "Clicks", "Cost"],
                                 date_from.isoformat(), date_to.isoformat())
        log("OK, строк:", len(rows))
    except Exception as e:
        log("ОШИБКА:", e)

    log("\n--- тест отчёта: + Sessions/Bounces/Conversions (как в fetch_direct.py) ---")
    try:
        head, rows = api.report("diag_full", ["Date", "CampaignId", "CampaignName", "CampaignType", "AdNetworkType",
                                               "Device", "Impressions", "Clicks", "Cost", "Sessions", "Bounces", "Conversions"],
                                 date_from.isoformat(), date_to.isoformat())
        log("OK, строк:", len(rows))
    except Exception as e:
        log("ОШИБКА:", e)


if __name__ == "__main__":
    main()
