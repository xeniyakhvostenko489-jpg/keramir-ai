#!/usr/bin/env python3
"""Fetch the daily campaign report from Yandex Direct and write data/direct.json.

Two numbers are collected for every row:
  conv  - achievements of all Metrika goals ("выполненные цели")
  leads - achievements of the goals listed as lead_goals in data/config.json
          (a form, a call request, a finished quiz), which is what the business
          actually counts as a lead.

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
KEY = ["Date", "CampaignId", "AdNetworkType", "Device"]   # чем строка отчёта о лидах цепляется к основной


def load_config():
    try:
        with open("data/config.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def lead_goal_ids(cfg):
    """Цели, достижение которых считается лидом."""
    raw = cfg.get("lead_goals") or cfg.get("goals") or []
    out = []
    for g in raw:
        gid = str(g.get("id") if isinstance(g, dict) else g)
        if gid.isdigit():
            out.append(gid)
    return out


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
    cfg = load_config()
    goals = lead_goal_ids(cfg)
    days = int(os.environ.get("DAYS", "200"))
    date_to = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    date_from = date_to - dt.timedelta(days=days - 1)
    log("Период %s .. %s, целей-лидов: %d" % (date_from, date_to, len(goals)))

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
            None,   # лиды, заполняются ниже
        ])
    fix_micros(rows, 8, 7)

    if goals:
        log("Отчёт по целям-лидам")
        lhead, lraw = api.report("km_leads", KEY + ["Conversions"], date_from.isoformat(),
                                 date_to.isoformat(), goals=goals)
        lix = {h: i for i, h in enumerate(lhead)}
        leads = {}
        for r in lraw:
            k = (r[lix["Date"]], r[lix["CampaignId"]], r[lix["AdNetworkType"]], r[lix["Device"]])
            leads[k] = (leads.get(k) or 0) + (num(r[lix["Conversions"]]) or 0)
        for r in rows:
            r[12] = leads.get((r[0], r[1], r[4], r[5]), 0)
        log("  строк с лидами: %d, всего лидов: %.0f" % (len(leads), sum(leads.values())))
    else:
        log("  список целей-лидов пуст, колонка лидов останется пустой")

    meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                          .isoformat().replace("+00:00", "Z"),
        "from": date_from.isoformat(), "to": date_to.isoformat(),
        "rows": len(rows),
        "campaigns": len({r[1] for r in rows}),
        "has_sessions": any(r[9] is not None for r in rows),
        "has_conversions": any(r[11] is not None for r in rows),
        "has_leads": any(r[12] for r in rows),
        "lead_goals": [{"id": g.get("id"), "name": g.get("name")} if isinstance(g, dict) else {"id": str(g)}
                       for g in (cfg.get("lead_goals") or [])],
        "goals": goals,
        "encrypted": bool(os.environ.get("DASH_KEY", "").strip()),
    }
    api.write_data(OUT, {"rows": rows}, meta)
    # В публичный лог идут только счётчики, не цифры расхода.
    log("Готово:", json.dumps({k: v for k, v in meta.items() if k != "lead_goals"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
