#!/usr/bin/env python3
"""Find the Metrika counters used by the campaigns and list their goals.

Also checks how a report looks when specific goal ids are requested, so we know
whether leads and all-goal conversions can come from one report or need two.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direct_api as api
from direct_api import log

METRIKA = "https://api-metrika.yandex.net/management/v1/counter/%s/goals"


def get(url, auth_scheme):
    req = urllib.request.Request(url, headers={"Authorization": "%s %s" % (auth_scheme, api.token()),
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def main():
    log("[1] Счётчики Метрики, привязанные к кампаниям")
    res = api.call("campaigns", "get", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "State"],
        "TextCampaignFieldNames": ["CounterIds"],
    })
    counters = set()
    for c in res.get("Campaigns", []):
        for cid in ((c.get("TextCampaign") or {}).get("CounterIds") or {}).get("Items", []) or []:
            counters.add(str(cid))
    log("  счётчики: %s" % (sorted(counters) or "не найдены в настройках кампаний"))

    log("\n[2] Список целей Метрики")
    goals = []
    for counter in sorted(counters) or ["10128436"]:
        for scheme in ("OAuth", "Bearer"):
            status, body = get(METRIKA % counter, scheme)
            log("  счётчик %s, схема %s: HTTP %s" % (counter, scheme, status))
            if status == 200:
                data = json.loads(body).get("goals", [])
                log("    целей: %d" % len(data))
                for g in data:
                    goals.append({"counter": counter, "id": str(g.get("id")), "name": g.get("name"),
                                  "type": g.get("type"), "price": g.get("default_price"),
                                  "flag": g.get("flag")})
                break
            log("    ответ: %s" % body[:200])
    if goals:
        log("\n  Все цели (id · цена · тип · название):")
        for g in sorted(goals, key=lambda x: -(x["price"] or 0)):
            log("    %-12s %8s %-14s %s" % (g["id"], (g["price"] or 0), g["type"] or "", g["name"]))
        with open("data/goals_raw.json", "w", encoding="utf-8") as f:
            json.dump(goals, f, ensure_ascii=False, indent=1)
        log("  сохранено в data/goals_raw.json")

    log("\n[3] Как выглядит отчёт, если запросить конкретные цели")
    sample = [g["id"] for g in goals if (g.get("price") or 0) > 0][:3]
    if not sample:
        sample = ["549858778", "549861006", "549932865"]
    log("  пробуем цели: %s" % sample)
    try:
        head, rows = api.report("goalprobe", ["Date", "CampaignId", "Clicks", "Cost", "Conversions"],
                                "2026-09-10", "2026-09-16", goals=sample)
        log("  колонки: %s" % head)
        log("  строк: %d" % len(rows))
        if rows:
            log("  первая строка: %s" % rows[0])
    except Exception as e:
        log("  ✗ %s" % e)


if __name__ == "__main__":
    main()
    probe_many()


def probe_many():
    """Сколько целей Директ принимает в одном отчёте."""
    exec(open("/tmp/goals.py").read(), globals())
    ids = [g[0] for g in GOALS]
    for n in (len(ids), 10):
        log("\n[4] Отчёт с %d целями" % n)
        try:
            head, rows = api.report("goals%d" % n, ["Date", "CampaignId", "Clicks", "Cost", "Conversions"],
                                    "2026-08-01", "2026-08-31", goals=ids[:n])
            cols = [c for c in head if c.startswith("Conversions")]
            tot = 0.0
            ix = {h: i for i, h in enumerate(head)}
            for r in rows:
                for c in cols:
                    v = r[ix[c]]
                    if v not in ("", "--"):
                        tot += float(v)
            log("  ✓ колонок с целями: %d, строк %d, сумма достижений за август: %.0f" % (len(cols), len(rows), tot))
            log("  первые колонки: %s" % head[:7])
            return
        except Exception as e:
            log("  ✗ %s" % e)
