#!/usr/bin/env python3
"""Pull the detail slices the dashboard needs beyond daily campaign totals.

Writes three files:
  data/slices.json    - campaign settings, geography, placements, slots, audience,
                        ad groups, ads and weekly per-ad statistics (for A/B tests)
  data/keywords.json  - keywords with traffic volume, for 30 and 90 day windows
  data/queries.json   - search queries, for 30 and 90 day windows

Environment: DIRECT_TOKEN (required), CLIENT_LOGIN, DASH_KEY,
             SLICES_MAX_AGE_HOURS (default 20), FORCE_SLICES=1 to ignore the age check.
"""
import datetime as dt
import json
import os
from collections import defaultdict

import direct_api as api
from direct_api import log, n0, num, micros

SLICES = "data/slices.json"
KEYWORDS = "data/keywords.json"
QUERIES = "data/queries.json"

# caps keep the published files small enough for a browser to load
CAP_GEO = 400
CAP_PLACEMENT = 400
CAP_QUERY_PAID = 7000      # queries that actually spent money
CAP_QUERY_NOCLICK = 1500   # high-impression queries with no clicks


def today():
    return dt.datetime.now(dt.timezone.utc).date()


def monday(d):
    return d - dt.timedelta(days=d.weekday())


def goals():
    try:
        with open("data/config.json", encoding="utf-8") as f:
            return [str(g) for g in json.load(f).get("goals", []) if str(g).isdigit()]
    except FileNotFoundError:
        return []


def fresh_enough():
    if os.environ.get("FORCE_SLICES") == "1":
        return False
    try:
        with open(SLICES, encoding="utf-8") as f:
            gen = json.load(f)["meta"]["generated_at"]
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(gen.replace("Z", "+00:00"))
        return age < dt.timedelta(hours=float(os.environ.get("SLICES_MAX_AGE_HOURS", "20")))
    except Exception:
        return False


def idx_of(head):
    return {name: i for i, name in enumerate(head)}


def roll(rows, head, key_fields, metric_fields):
    """Aggregates report rows by the given key fields."""
    ix = idx_of(head)
    acc = defaultdict(lambda: [0.0] * len(metric_fields))
    for r in rows:
        key = tuple(r[ix[f]] for f in key_fields)
        a = acc[key]
        for i, m in enumerate(metric_fields):
            a[i] += n0(r[ix[m]]) if m in ix else 0.0
    return acc


def r2(x):
    return round(x, 2)


FAILED = []


def safe(label, fn, fallback):
    """Runs one slice; a failure is reported and does not stop the rest."""
    try:
        return fn()
    except Exception as e:
        log("  ✗ %s: %s" % (label, e))
        FAILED.append("%s: %s" % (label, e))
        return fallback


# ---------------------------------------------------------------- campaigns

STRATEGY_RU = {
    "HIGHEST_POSITION": "наивысшая доступная позиция",
    "AVERAGE_CPC": "средняя цена клика",
    "AVERAGE_CPA": "средняя цена конверсии",
    "AVERAGE_ROI": "средняя рентабельность",
    "WB_MAXIMUM_CLICKS": "макс. кликов за неделю",
    "WB_MAXIMUM_CONVERSION_RATE": "макс. конверсий за неделю",
    "WB_MAXIMUM_APP_INSTALLS": "макс. установок за неделю",
    "PAY_FOR_CONVERSION": "оплата за конверсии",
    "PAY_FOR_CONVERSION_CRR": "оплата за конверсии, ДРР",
    "MAXIMUM_IMPRESSIONS": "максимум показов",
    "MAXIMUM_COVERAGE": "максимальный охват",
    "MANUAL_CPM": "ручное управление CPM",
    "SERVING_OFF": "показы отключены",
    "NETWORK_DEFAULT": "как на поиске",
    "MAXIMUM_CLICKS": "максимум кликов",
    "MAXIMUM_CONVERSION_RATE": "максимум конверсий",
    "AUTOBUDGET": "автобюджет",
    "AUTOBUDGET_AVG_CPC": "автобюджет, средняя цена клика",
    "AUTOBUDGET_AVG_CPA": "автобюджет, средняя цена конверсии",
}


def fetch_campaigns():
    log("Кампании: настройки и стратегии")
    res = api.call("campaigns", "get", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "Status", "State", "StatusPayment", "StatusClarification",
                       "DailyBudget", "Funds", "StartDate", "EndDate", "TimeTargeting", "Type"],
        "TextCampaignFieldNames": ["BiddingStrategy"],
    })
    out = []
    for c in res.get("Campaigns", []):
        db = c.get("DailyBudget") or {}
        funds = c.get("Funds") or {}
        cf = funds.get("CampaignFunds") or {}
        sa = funds.get("SharedAccountFunds") or {}
        strat = ((c.get("TextCampaign") or {}).get("BiddingStrategy") or {})
        srch = strat.get("Search") or {}
        netw = strat.get("Network") or {}
        st = srch.get("BiddingStrategyType")
        # the strategy's own money caps, whatever nested block carries them
        limits = {}
        for block in srch.values():
            if isinstance(block, dict):
                for k in ("AverageCpc", "AverageCpa", "WeeklySpendLimit", "BidCeiling", "WeeklyBudget"):
                    if k in block:
                        limits[k] = micros(block[k])
        tt = c.get("TimeTargeting") or {}
        out.append({
            "id": str(c["Id"]), "name": c.get("Name"), "status": c.get("Status"),
            "state": c.get("State"), "payment": c.get("StatusPayment"),
            "clarification": (c.get("StatusClarification") or "")[:200],
            "type": c.get("Type"),
            "dailyBudget": micros(db.get("Amount")) if db.get("Amount") else None,
            "dailyBudgetMode": db.get("Mode"),
            "fundsMode": funds.get("Mode"),
            "balance": micros(cf.get("Balance")) if cf.get("Balance") is not None else None,
            "sharedSpend": micros(sa.get("Spend")) if sa.get("Spend") is not None else None,
            "strategySearch": st, "strategySearchRu": STRATEGY_RU.get(st, st),
            "strategyNetwork": netw.get("BiddingStrategyType"),
            "strategyNetworkRu": STRATEGY_RU.get(netw.get("BiddingStrategyType"), netw.get("BiddingStrategyType")),
            "strategyLimits": limits,
            "startDate": c.get("StartDate"), "endDate": c.get("EndDate"),
            "timeTargetingSchedule": bool(tt.get("Schedule")),
            "holidaysOff": (tt.get("HolidaysSchedule") or {}).get("SuspendOnHolidays"),
        })
    log("  кампаний: %d" % len(out))
    return out


# ---------------------------------------------------------------- ads

def fetch_ads():
    log("Объявления и группы")
    groups = api.get_all("adgroups", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "CampaignId", "Name", "Status", "Type"]}, "AdGroups")
    ads = api.get_all("ads", {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status"],
        "TextAdFieldNames": ["Title", "Title2", "Text"]}, "Ads")
    g = [[str(x["Id"]), str(x["CampaignId"]), x.get("Name", ""), x.get("Status", "")] for x in groups]
    a = []
    for x in ads:
        t = x.get("TextAd") or {}
        a.append([str(x["Id"]), str(x["AdGroupId"]), str(x["CampaignId"]), x.get("Type", ""),
                  x.get("Status", ""), x.get("State", ""),
                  t.get("Title", ""), t.get("Title2", "") or "", (t.get("Text", "") or "")[:200]])
    log("  групп: %d, объявлений: %d" % (len(g), len(a)))
    return g, a


def fetch_ad_weeks(date_from, date_to, gs):
    log("Статистика объявлений по неделям (для A/B)")
    head, rows = api.report("adweeks", ["Date", "AdId", "AdGroupId", "CampaignId",
                                        "Impressions", "Clicks", "Cost", "Conversions"],
                            date_from, date_to, report_type="AD_PERFORMANCE_REPORT", goals=gs)
    if not rows:
        return []
    ix = idx_of(head)
    acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for r in rows:
        wk = monday(dt.date.fromisoformat(r[ix["Date"]])).isoformat()
        k = (r[ix["AdId"]], r[ix["AdGroupId"]], wk)
        a = acc[k]
        a[0] += n0(r[ix["Impressions"]]); a[1] += n0(r[ix["Clicks"]])
        a[2] += n0(r[ix["Cost"]]); a[3] += n0(r[ix["Conversions"]])
    out = [[k[0], k[1], k[2], int(v[0]), int(v[1]), r2(v[2]), int(v[3])]
           for k, v in acc.items() if v[0] > 0]
    log("  строк объявление×неделя: %d" % len(out))
    return out


# ---------------------------------------------------------------- slices per window

def fetch_geo(date_from, date_to, gs):
    head, rows = api.report("geo", ["LocationOfPresenceName", "TargetingLocationName",
                                    "Impressions", "Clicks", "Cost", "Conversions"],
                            date_from, date_to, goals=gs)
    metrics = ["Impressions", "Clicks", "Cost", "Conversions"]
    pres = roll(rows, head, ["LocationOfPresenceName"], metrics) if rows else {}
    targ = roll(rows, head, ["TargetingLocationName"], metrics) if rows else {}
    fmt = lambda acc, cap: sorted(
        ([k[0], int(v[0]), int(v[1]), r2(v[2]), int(v[3])] for k, v in acc.items()),
        key=lambda r: -r[3])[:cap]
    return fmt(pres, CAP_GEO), fmt(targ, 200)


def fetch_placements(date_from, date_to, gs):
    head, rows = api.report("placements", ["Placement", "AdNetworkType", "Slot",
                                           "Impressions", "Clicks", "Cost", "Conversions"],
                            date_from, date_to, goals=gs)
    metrics = ["Impressions", "Clicks", "Cost", "Conversions"]
    pl = roll(rows, head, ["Placement", "AdNetworkType"], metrics) if rows else {}
    sl = roll(rows, head, ["Slot"], metrics) if rows else {}
    places = sorted(([k[0], k[1], int(v[0]), int(v[1]), r2(v[2]), int(v[3])] for k, v in pl.items()),
                    key=lambda r: -r[4])[:CAP_PLACEMENT]
    slots = sorted(([k[0], int(v[0]), int(v[1]), r2(v[2]), int(v[3])] for k, v in sl.items()),
                   key=lambda r: -r[3])
    return places, slots


def fetch_demo(date_from, date_to, gs):
    head, rows = api.report("demo", ["Gender", "Age", "Impressions", "Clicks", "Cost", "Conversions"],
                            date_from, date_to, goals=gs)
    if not rows:
        return []
    acc = roll(rows, head, ["Gender", "Age"], ["Impressions", "Clicks", "Cost", "Conversions"])
    return sorted(([k[0], k[1], int(v[0]), int(v[1]), r2(v[2]), int(v[3])] for k, v in acc.items()),
                  key=lambda r: -r[4])


def fetch_keywords(date_from, date_to, gs):
    head, rows = api.report("criteria", ["CampaignId", "AdGroupId", "CriterionId", "Criterion",
                                         "CriterionType", "Impressions", "Clicks", "Cost",
                                         "Conversions", "AvgTrafficVolume", "WeightedCtr",
                                         "WeightedImpressions"],
                            date_from, date_to, report_type="CRITERIA_PERFORMANCE_REPORT", goals=gs)
    if not rows:
        return []
    ix = idx_of(head)
    acc = {}
    for r in rows:
        k = r[ix["CriterionId"]]
        a = acc.get(k)
        if a is None:
            a = acc[k] = {"crit": r[ix["Criterion"]], "type": r[ix["CriterionType"]],
                          "camp": r[ix["CampaignId"]], "grp": r[ix["AdGroupId"]],
                          "impr": 0.0, "clicks": 0.0, "cost": 0.0, "conv": 0.0,
                          "tvSum": 0.0, "tvW": 0.0, "wImpr": 0.0, "wCtrSum": 0.0}
        impr = n0(r[ix["Impressions"]])
        a["impr"] += impr; a["clicks"] += n0(r[ix["Clicks"]])
        a["cost"] += n0(r[ix["Cost"]]); a["conv"] += n0(r[ix["Conversions"]])
        tv = num(r[ix["AvgTrafficVolume"]])
        if tv is not None and impr > 0:
            a["tvSum"] += tv * impr; a["tvW"] += impr
        wi = n0(r[ix["WeightedImpressions"]]); wc = num(r[ix["WeightedCtr"]])
        a["wImpr"] += wi
        if wc is not None:
            a["wCtrSum"] += wc * wi
    out = []
    for cid, a in acc.items():
        out.append([cid, a["crit"][:120], a["camp"], a["grp"], a["type"],
                    int(a["impr"]), int(a["clicks"]), r2(a["cost"]), int(a["conv"]),
                    r2(a["tvSum"] / a["tvW"]) if a["tvW"] else None,
                    r2(a["wCtrSum"] / a["wImpr"]) if a["wImpr"] else None])
    out.sort(key=lambda r: -r[7])
    log("  ключевых фраз: %d" % len(out))
    return out


def fetch_queries(date_from, date_to, gs):
    head, rows = api.report("queries", ["Query", "MatchType", "Criterion", "CampaignId",
                                        "Impressions", "Clicks", "Cost", "Conversions"],
                            date_from, date_to, report_type="SEARCH_QUERY_PERFORMANCE_REPORT",
                            goals=gs)
    if not rows:
        return []
    ix = idx_of(head)
    acc = {}
    for r in rows:
        k = (r[ix["Query"]][:120], r[ix["CampaignId"]])
        a = acc.get(k)
        if a is None:
            a = acc[k] = {"match": r[ix["MatchType"]], "crit": r[ix["Criterion"]][:100],
                          "impr": 0.0, "clicks": 0.0, "cost": 0.0, "conv": 0.0}
        a["impr"] += n0(r[ix["Impressions"]]); a["clicks"] += n0(r[ix["Clicks"]])
        a["cost"] += n0(r[ix["Cost"]]); a["conv"] += n0(r[ix["Conversions"]])
    paid, noclick = [], []
    for (q, camp), a in acc.items():
        row = [q, camp, a["match"], a["crit"], int(a["impr"]), int(a["clicks"]),
               r2(a["cost"]), int(a["conv"])]
        if a["cost"] > 0 or a["conv"] > 0:
            paid.append(row)
        elif a["impr"] >= 30:
            noclick.append(row)
    paid.sort(key=lambda r: -r[6])
    noclick.sort(key=lambda r: -r[4])
    out = paid[:CAP_QUERY_PAID] + noclick[:CAP_QUERY_NOCLICK]
    log("  запросов: всего уникальных %d, сохранено %d (с расходом %d)"
        % (len(acc), len(out), len(paid)))
    return out


# ---------------------------------------------------------------- main

def main():
    api.token()
    if fresh_enough():
        log("slices.json свежий, пропускаем")
        return
    gs = goals()
    to = today() - dt.timedelta(days=1)
    w = {"w30": ((to - dt.timedelta(days=29)).isoformat(), to.isoformat()),
         "w90": ((to - dt.timedelta(days=89)).isoformat(), to.isoformat())}
    log("Окна: 30 дней %s..%s, 90 дней %s..%s, цели: %s"
        % (w["w30"][0], w["w30"][1], w["w90"][0], w["w90"][1], gs or "по умолчанию"))

    campaigns = safe("кампании", fetch_campaigns, [])
    groups, ads = safe("объявления", fetch_ads, ([], []))
    ad_weeks = safe("статистика объявлений", lambda: fetch_ad_weeks(w["w90"][0], w["w90"][1], gs), [])

    geo_p, geo_t, places, slots, demo = {}, {}, {}, {}, {}
    for name, (f, t) in w.items():
        log("Срезы за %s (%s..%s)" % (name, f, t))
        geo_p[name], geo_t[name] = safe("гео %s" % name, lambda f=f, t=t: fetch_geo(f, t, gs), ([], []))
        places[name], slots[name] = safe("площадки %s" % name, lambda f=f, t=t: fetch_placements(f, t, gs), ([], []))
        demo[name] = safe("аудитория %s" % name, lambda f=f, t=t: fetch_demo(f, t, gs), [])

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    enc = bool(os.environ.get("DASH_KEY", "").strip())
    base_meta = {"generated_at": now, "windows": w, "goals": gs, "encrypted": enc, "failed": FAILED}

    api.write_data(SLICES, {
        "campaigns": campaigns, "adgroups": groups, "ads": ads, "adWeeks": ad_weeks,
        "geoPresence": geo_p, "geoTargeting": geo_t,
        "placements": places, "slots": slots, "demo": demo,
    }, dict(base_meta, campaigns=len(campaigns), ads=len(ads), adWeeks=len(ad_weeks)))

    kw = {}
    for name, (f, t) in w.items():
        log("Ключевые фразы за %s" % name)
        kw[name] = safe("ключевые фразы %s" % name, lambda f=f, t=t: fetch_keywords(f, t, gs), [])
    api.write_data(KEYWORDS, kw, dict(base_meta, rows={k: len(v) for k, v in kw.items()}))

    qs = {}
    for name, (f, t) in w.items():
        log("Поисковые запросы за %s" % name)
        qs[name] = safe("поисковые запросы %s" % name, lambda f=f, t=t: fetch_queries(f, t, gs), [])
    api.write_data(QUERIES, qs, dict(base_meta, rows={k: len(v) for k, v in qs.items()}))
    if FAILED:
        log("Частично не получилось: %s" % "; ".join(FAILED))
    log("Готово.")


if __name__ == "__main__":
    main()
