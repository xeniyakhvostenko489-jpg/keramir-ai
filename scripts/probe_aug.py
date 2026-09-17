#!/usr/bin/env python3
"""Сверка с ручным отчётом за август: 525 заявок.

Считает август по каждой известной цели, по всем целям сразу, проверяет
чувствительность к модели атрибуции и выясняет, ругается ли Директ на
неизвестный идентификатор цели (от этого зависит, можно ли искать цели перебором).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direct_api as api
from direct_api import log, n0

FROM, TO = "2026-08-01", "2026-08-31"


def goal_list():
    with open("data/config.json", encoding="utf-8") as f:
        return [(g["id"], g.get("name", "")) for g in json.load(f)["lead_goals"]]


def total(head, rows, prefix="Conversions"):
    ix = {h: i for i, h in enumerate(head)}
    cols = [h for h in head if h.startswith(prefix)]
    per = {}
    for c in cols:
        per[c] = sum(n0(r[ix[c]]) for r in rows)
    return per


def main():
    goals = goal_list()
    names = dict(goals)
    ids = [g[0] for g in goals]

    log("[1] Август целиком, все цели")
    head, rows = api.report("aug_all", ["Date", "CampaignId", "Clicks", "Cost", "Conversions"], FROM, TO)
    ix = {h: i for i, h in enumerate(head)}
    clicks = sum(n0(r[ix["Clicks"]]) for r in rows)
    cost = sum(n0(r[ix["Cost"]]) for r in rows)
    conv = sum(n0(r[ix["Conversions"]]) for r in rows)
    log("  клики %.0f · расход %.0f ₽ · достижения всех целей %.0f" % (clicks, cost, conv))

    log("\n[2] Август по каждой цели из списка (атрибуция AUTO)")
    per = {}
    for i in range(0, len(ids), 10):
        h, r = api.report("aug_g%d" % i, ["Date", "CampaignId", "Conversions"], FROM, TO, goals=ids[i:i + 10])
        for col, v in total(h, r).items():
            per[col.split("_")[1]] = v
    for gid, v in sorted(per.items(), key=lambda kv: -kv[1]):
        log("  %-12s %6.0f  %s" % (gid, v, names.get(gid, "")))
    log("  ИТОГО по списку: %.0f" % sum(per.values()))
    log("  Ручной отчёт: 525. Не хватает: %.0f" % (525 - sum(per.values())))

    log("\n[3] Чувствительность к модели атрибуции (первые 5 целей)")
    for model in ("AUTO", "LAST_SIGNIFICANT_CLICK", "LAST_YANDEX_DIRECT_CLICK_CROSS_DEVICE", "FIRST_CLICK"):
        try:
            params_goals = ids[:5]
            name = "aug_attr_%s" % model.lower()[:12]
            h, r = _report_attr(name, params_goals, model)
            log("  %-38s %.0f" % (model, sum(total(h, r).values())))
        except Exception as e:
            log("  %-38s ✗ %s" % (model, str(e)[:110]))

    log("\n[4] Ругается ли Директ на неизвестную цель")
    try:
        api.report("aug_fake", ["Date", "Conversions"], FROM, TO, goals=["999999999"])
        log("  нет: неизвестный идентификатор принимается молча, перебором цели не найти")
    except Exception as e:
        log("  да: %s" % str(e)[:200])


def _report_attr(name, goals, model):
    """Тот же отчёт, но с явной моделью атрибуции."""
    import direct_api as a
    orig = a._report_once

    def patched(n, fields, df, dt_, rt="CUSTOM_REPORT", g=None, criteria=None, vat=True):
        return orig(n, fields, df, dt_, rt, g, criteria, vat)

    params = {
        "SelectionCriteria": {"DateFrom": FROM, "DateTo": TO},
        "FieldNames": ["Date", "CampaignId", "Conversions"],
        "ReportName": "km_%s_%s" % (name, model),
        "ReportType": "CUSTOM_REPORT", "DateRangeType": "CUSTOM_DATE",
        "Format": "TSV", "IncludeVAT": "YES",
        "Goals": goals, "AttributionModels": [model],
    }
    extra = {"processingMode": "auto", "returnMoneyInMicros": "false",
             "skipReportHeader": "true", "skipReportSummary": "true"}
    import time
    for _ in range(40):
        status, hdrs, text = a.post("reports", {"params": params}, extra)
        if status in (201, 202):
            time.sleep(min(max(int(hdrs.get("retryIn", "5") or 5), 2), 20))
            continue
        break
    if status != 200:
        raise RuntimeError(a._error(text, status))
    lines = [l for l in text.splitlines() if l]
    if lines and lines[0].startswith('"'):
        lines.pop(0)
    if lines and lines[-1].startswith("Total"):
        lines.pop()
    head = lines.pop(0).split("\t")
    return head, [l.split("\t") for l in lines]


if __name__ == "__main__":
    main()
