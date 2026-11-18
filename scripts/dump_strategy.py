#!/usr/bin/env python3
"""One-off diagnostic: dump the RAW, unprocessed BiddingStrategy JSON for a
handful of campaigns, byte for byte as the Direct API returns it.

Written to check a specific suspicion: fetch_slices.py's strategyGoals
extraction walks every nested dict under Search/Network looking for a
"GoalId" key, on the assumption that key only ever means a Metrika goal.
If a campaign uses a shared "пакетная стратегия" (a strategy object shared
across several campaigns), the API may expose an internal strategy/package
id under a key our generic walk also treats as a goal id, producing a
plausible-looking but wrong small number (e.g. "13") that doesn't match any
real Metrika goal. This script bypasses that extraction entirely and prints
the raw structure so the true field names are visible.

Not part of the regular dashboard pipeline. Writes <DATA_DIR>/strategy_dump.json.

Environment: DIRECT_TOKEN (required), CLIENT_LOGIN, DATA_DIR, CAMPAIGN_IDS
             (comma-separated; defaults to the 8 RSYA campaigns under review).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direct_api as api
from direct_api import log

DATA_DIR = os.environ.get("DATA_DIR", "data").rstrip("/")
OUT = DATA_DIR + "/strategy_dump.json"

DEFAULT_IDS = [
    "708903181", "709594477", "709632644", "709636177",
    "709655086", "709655113", "709655358", "709655409",
]


def main():
    api.token()
    ids = [s.strip() for s in os.environ.get("CAMPAIGN_IDS", "").split(",") if s.strip()] or DEFAULT_IDS
    res = api.call("campaigns", "get", {
        "SelectionCriteria": {"Ids": [int(i) for i in ids]},
        "FieldNames": ["Id", "Name", "Status", "State"],
        "TextCampaignFieldNames": ["BiddingStrategy"],
    })
    out = []
    for c in res.get("Campaigns", []):
        out.append({
            "id": str(c.get("Id")),
            "name": c.get("Name"),
            "raw_TextCampaign": c.get("TextCampaign"),
        })
        log("  %s %s" % (c.get("Id"), c.get("Name")))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"campaigns": out}, f, ensure_ascii=False, indent=1)
    log("Готово: %d кампаний, written %s" % (len(out), OUT))


if __name__ == "__main__":
    main()
