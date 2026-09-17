#!/usr/bin/env python3
"""Достижения по каждой цели, по дням — чтобы в дашборде их можно было складывать.

Пишет data/goals.json: список целей и разреженная таблица [индекс цели, дата, достижений].
Нулевые дни не сохраняются, поэтому файл остаётся маленьким даже на сотне целей.

Цели берутся из data/config.json:
  lead_goals  - цели, которые считаются лидом (и отслеживаются по умолчанию)
  track_goals - дополнительные цели, которые нужно видеть в статистике, но не считать лидом

Environment: DIRECT_TOKEN (обязательно), CLIENT_LOGIN, DASH_KEY,
             DAYS (по умолчанию 400), GOALS_MAX_AGE_HOURS (по умолчанию 20), FORCE_GOALS=1.
"""
import datetime as dt
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direct_api as api
from direct_api import log, n0

OUT = "data/goals.json"


def tracked_goals(cfg):
    """Список целей для статистики: сначала лиды, затем остальные отслеживаемые."""
    seen, out = set(), []
    for key, is_lead in (("lead_goals", True), ("track_goals", False)):
        for g in cfg.get(key) or []:
            gid = str(g.get("id") if isinstance(g, dict) else g)
            if not gid.isdigit() or gid in seen:
                continue
            seen.add(gid)
            out.append({"id": gid,
                        "name": (g.get("name") if isinstance(g, dict) else "") or ("Цель " + gid),
                        "lead": is_lead})
    return out


def fresh_enough():
    if os.environ.get("FORCE_GOALS") == "1":
        return False
    try:
        with open(OUT, encoding="utf-8") as f:
            gen = json.load(f)["meta"]["generated_at"]
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(gen.replace("Z", "+00:00"))
        return age < dt.timedelta(hours=float(os.environ.get("GOALS_MAX_AGE_HOURS", "20")))
    except Exception:
        return False


def main():
    api.token()
    if fresh_enough():
        log("goals.json свежий, пропускаем")
        return
    try:
        with open("data/config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    goals = tracked_goals(cfg)
    if not goals:
        log("Целей для отслеживания нет, файл не пишем")
        return

    days = int(os.environ.get("DAYS", "400"))
    to = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    frm = to - dt.timedelta(days=days - 1)
    log("Достижения по %d целям за %s .. %s" % (len(goals), frm, to))

    idx = {g["id"]: i for i, g in enumerate(goals)}
    daily = defaultdict(float)
    ids = [g["id"] for g in goals]
    for i in range(0, len(ids), api.GOALS_PER_REPORT):
        chunk = ids[i:i + api.GOALS_PER_REPORT]
        head, rows = api._report_once("km_goals_%d" % (i // api.GOALS_PER_REPORT),
                                      ["Date", "Conversions"], frm.isoformat(), to.isoformat(),
                                      goals=chunk)
        ix = {h: j for j, h in enumerate(head)}
        cols = [(h, h.split("_")[1]) for h in head if h.startswith("Conversions")]
        for r in rows:
            date = r[ix["Date"]]
            for col, gid in cols:
                v = n0(r[ix[col]])
                if v:
                    daily[(idx[gid], date)] += v
        log("  часть %d: целей %d, строк отчёта %d" % (i // api.GOALS_PER_REPORT + 1, len(chunk), len(rows)))

    table = sorted(([gi, d, int(v)] for (gi, d), v in daily.items() if v), key=lambda r: (r[1], r[0]))
    totals = defaultdict(float)
    for gi, _, v in table:
        totals[gi] += v
    log("\nВсего за период по каждой цели:")
    for g, i in sorted(idx.items(), key=lambda kv: -totals[kv[1]]):
        log("  %-12s %7.0f  %s%s" % (g, totals[i], goals[i]["name"], "" if goals[i]["lead"] else "  (не лид)"))

    meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                          .isoformat().replace("+00:00", "Z"),
        "from": frm.isoformat(), "to": to.isoformat(),
        "goals": len(goals), "rows": len(table),
        "total": int(sum(totals.values())),
        "encrypted": bool(os.environ.get("DASH_KEY", "").strip()),
    }
    api.write_data(OUT, {"goals": goals, "daily": table}, meta)
    log("Готово: целей %d, строк %d" % (len(goals), len(table)))


if __name__ == "__main__":
    main()
