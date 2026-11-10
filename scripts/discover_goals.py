#!/usr/bin/env python3
"""One-off: which Metrika goal IDs are set as strategy targets on active campaigns,
and how many times each fired recently. Metrika doesn't give goal names through the
Direct API, so this only resolves IDs — matching names has to happen by hand in the
Metrika UI (Report Goals).

Writes <DATA_DIR>/goal_discovery.json. Not part of the regular dashboard data;
run manually via workflow_dispatch when you need to re-check which goals exist.

Environment: DIRECT_TOKEN (required), CLIENT_LOGIN, DAYS (default 30)
"""
import datetime as dt
import json
import os
from collections import defaultdict

import direct_api as api
from direct_api import log

DATA_DIR = os.environ.get("DATA_DIR", "data").rstrip("/")
OUT = DATA_DIR + "/goal_discovery.json"


def fetch_campaigns():
    res = api.call("campaigns", "get", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "Status", "State"],
        "TextCampaignFieldNames": ["BiddingStrategy"],
    })
    out = []
    for c in res.get("Campaigns", []):
        if c.get("State") == "ARCHIVED":
            continue
        strat = ((c.get("TextCampaign") or {}).get("BiddingStrategy") or {})
        goal_ids = set()
        for side in ("Search", "Network"):
            for block in (strat.get(side) or {}).values():
                if not isinstance(block, dict):
                    continue
                if block.get("GoalId"):
                    goal_ids.add(str(block["GoalId"]))
                for g in block.get("Goals") or []:
                    if isinstance(g, dict) and g.get("GoalId"):
                        goal_ids.add(str(g["GoalId"]))
        out.append({"id": str(c["Id"]), "name": c.get("Name"), "status": c.get("Status"),
                    "goals": sorted(goal_ids)})
    return out


def main():
    api.token()
    camps = fetch_campaigns()
    all_goals = sorted({g for c in camps for g in c["goals"]})
    log("кампаний активных: %d, уникальных целей в стратегиях: %d" % (len(camps), len(all_goals)))

    by_campaign = defaultdict(list)
    for c in camps:
        for g in c["goals"]:
            by_campaign[g].append(c["name"])

    counts = {}
    if all_goals:
        days = int(os.environ.get("DAYS", "30"))
        to = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        frm = to - dt.timedelta(days=days - 1)
        log("Достижения по %d целям за %s .. %s" % (len(all_goals), frm, to))
        for i in range(0, len(all_goals), api.GOALS_PER_REPORT):
            chunk = all_goals[i:i + api.GOALS_PER_REPORT]
            head, rows = api._report_once("km_discover_%d" % (i // api.GOALS_PER_REPORT),
                                          ["Date", "Conversions"], frm.isoformat(), to.isoformat(), goals=chunk)
            ix = {h: j for j, h in enumerate(head)}
            for gid in chunk:
                col = "Conversions_%s" % gid
                total = 0.0
                if col in ix:
                    for r in rows:
                        v = r[ix[col]]
                        if v not in ("", "--"):
                            total += float(v)
                counts[gid] = int(total)

    out = {
        "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                 "days": int(os.environ.get("DAYS", "30")), "campaigns_active": len(camps)},
        "goals": [{"goal_id": g, "achievements": counts.get(g, 0), "campaigns": by_campaign[g]} for g in all_goals],
        "campaigns_without_goals": [c["name"] for c in camps if not c["goals"]],
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log("written:", OUT)


if __name__ == "__main__":
    main()
