#!/usr/bin/env python3
"""Сколько достижений даёт каждая цель-лид за 90 дней, по чанкам по 10."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direct_api as api
from direct_api import log
from lead_goals_seed import GOALS

FROM, TO = "2026-06-19", "2026-09-16"


def main():
    names = {g[0]: g[1] for g in GOALS}
    ids = [g[0] for g in GOALS]
    totals = {}
    for i in range(0, len(ids), 10):
        chunk = ids[i:i + 10]
        head, rows = api.report("leadchunk%d" % i, ["Date", "CampaignId", "Clicks", "Conversions"],
                                FROM, TO, goals=chunk)
        ix = {h: j for j, h in enumerate(head)}
        for col in [h for h in head if h.startswith("Conversions")]:
            gid = col.split("_")[1]
            t = 0.0
            for r in rows:
                v = r[ix[col]]
                if v not in ("", "--"):
                    t += float(v)
            totals[gid] = t
        log("  чанк %d: строк %d" % (i // 10 + 1, len(rows)))
    log("\nДостижения по целям-лидам за %s..%s:" % (FROM, TO))
    for gid, t in sorted(totals.items(), key=lambda kv: -kv[1]):
        log("  %-12s %8.0f  %s" % (gid, t, names.get(gid, "")))
    log("\nИТОГО лидов за 90 дней: %.0f" % sum(totals.values()))

    head, rows = api.report("allconv", ["Date", "CampaignId", "Clicks", "Conversions"], FROM, TO)
    ix = {h: j for j, h in enumerate(head)}
    allc = sum(float(r[ix["Conversions"]]) for r in rows if r[ix["Conversions"]] not in ("", "--"))
    clicks = sum(float(r[ix["Clicks"]]) for r in rows if r[ix["Clicks"]] not in ("", "--"))
    log("Все цели за тот же период: %.0f, кликов %.0f" % (allc, clicks))
    log("Доля настоящих лидов от всех достижений целей: %.1f%%" % (100 * sum(totals.values()) / allc if allc else 0))


if __name__ == "__main__":
    main()
