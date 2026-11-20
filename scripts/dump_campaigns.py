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


if __name__ == "__main__":
    main()
