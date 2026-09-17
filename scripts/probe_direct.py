#!/usr/bin/env python3
"""Probe which Yandex Direct report types and fields are available for this account.

Prints only structural information: which requests succeed, which fields come back
and how often they are filled. No money figures, campaign names or query texts are
printed, because Actions logs of a public repository are public.
"""
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.direct.yandex.com/json/v5/"
TOKEN = os.environ.get("DIRECT_TOKEN", "").strip()
LOGIN = os.environ.get("CLIENT_LOGIN", "").strip()
TO = (dt.datetime.utcnow().date() - dt.timedelta(days=1))
FROM = TO - dt.timedelta(days=13)


def log(*a):
    print(*a, flush=True)


def headers(extra=None):
    h = {"Authorization": "Bearer " + TOKEN, "Accept-Language": "ru",
         "Content-Type": "application/json; charset=utf-8"}
    if LOGIN:
        h["Client-Login"] = LOGIN
    h.update(extra or {})
    return h


def post(path, body, extra=None, timeout=120):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers=headers(extra), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


def err_text(text):
    try:
        e = json.loads(text).get("error", {})
        return "%s / %s / %s" % (e.get("error_code"), e.get("error_string"), e.get("error_detail"))
    except Exception:
        return text[:200]


def report(name, fields, report_type="CUSTOM_REPORT", goals=None, extra_criteria=None):
    params = {
        "SelectionCriteria": {"DateFrom": FROM.isoformat(), "DateTo": TO.isoformat()},
        "FieldNames": fields,
        "ReportName": "probe_%s_%d" % (name, int(time.time())),
        "ReportType": report_type,
        "DateRangeType": "CUSTOM_DATE",
        "Format": "TSV",
        "IncludeVAT": "YES",
    }
    if extra_criteria:
        params["SelectionCriteria"].update(extra_criteria)
    if goals:
        params["Goals"] = goals
        params["AttributionModels"] = ["AUTO"]
    extra = {"processingMode": "auto", "returnMoneyInMicros": "false",
             "skipReportHeader": "true", "skipReportSummary": "true"}
    for _ in range(30):
        status, hdrs, text = post("reports", {"params": params}, extra)
        if status in (201, 202):
            time.sleep(min(max(int(hdrs.get("retryIn", "5") or 5), 2), 20))
            continue
        break
    if status != 200:
        log("  ✗ HTTP %s — %s" % (status, err_text(text)))
        return None
    lines = [l for l in text.splitlines() if l]
    if not lines:
        log("  ✓ запрос принят, но строк нет")
        return []
    head = lines[0].split("\t")
    rows = [l.split("\t") for l in lines[1:] if not l.startswith("Total")]
    log("  ✓ строк: %d" % len(rows))
    for i, col in enumerate(head):
        vals = [r[i] for r in rows if i < len(r)]
        filled = [v for v in vals if v not in ("", "--")]
        uniq = len(set(filled))
        nonzero = sum(1 for v in filled if v not in ("0", "0.00", "0.0"))
        log("     %-28s заполнено %4d/%-4d уникальных %-5d ненулевых %d"
            % (col, len(filled), len(vals), uniq, nonzero))
    return rows


def main():
    if not TOKEN:
        raise SystemExit("DIRECT_TOKEN is not set")
    log("Период проверки: %s .. %s" % (FROM, TO))

    log("\n[1] Регионы присутствия и таргетинга")
    report("geo", ["Date", "CampaignId", "LocationOfPresenceName", "TargetingLocationName",
                   "Impressions", "Clicks", "Cost", "Conversions"])

    log("\n[2] Поисковые запросы (SEARCH_QUERY_PERFORMANCE_REPORT)")
    report("queries", ["Query", "MatchType", "Criterion", "CampaignId",
                       "Impressions", "Clicks", "Cost", "Conversions"],
           report_type="SEARCH_QUERY_PERFORMANCE_REPORT")

    log("\n[3] Ключевые фразы и объём трафика (CRITERIA_PERFORMANCE_REPORT)")
    report("criteria", ["CampaignId", "AdGroupId", "CriterionId", "Criterion", "CriterionType",
                        "Impressions", "Clicks", "Cost", "Conversions",
                        "AvgTrafficVolume", "WeightedCtr", "WeightedImpressions"],
           report_type="CRITERIA_PERFORMANCE_REPORT")

    log("\n[4] Объявления (AD_PERFORMANCE_REPORT)")
    report("ads", ["CampaignId", "AdGroupId", "AdId", "Impressions", "Clicks", "Cost", "Conversions"],
           report_type="AD_PERFORMANCE_REPORT")

    log("\n[5] Позиция показа (Slot) и площадки РСЯ (Placement)")
    report("slot", ["Date", "CampaignId", "Slot", "Placement", "AdNetworkType",
                    "Impressions", "Clicks", "Cost"])

    log("\n[6] Пол и возраст")
    report("demo", ["CampaignId", "Gender", "Age", "Impressions", "Clicks", "Cost", "Conversions"])

    log("\n[7] Выручка из электронной коммерции Метрики (Revenue, GoalsRoi, Profit)")
    report("revenue", ["Date", "CampaignId", "Clicks", "Cost", "Conversions", "Revenue", "GoalsRoi", "Profit"])

    log("\n[8] Глубина просмотра и качество трафика (AvgPageviews)")
    report("quality", ["Date", "CampaignId", "Sessions", "AvgPageviews", "BounceRate"])

    log("\n[9] Тип клика и мобильная платформа")
    report("clicktype", ["CampaignId", "ClickType", "MobilePlatform", "CarrierType", "Impressions", "Clicks", "Cost"])

    log("\n[10] Настройки кампаний: дневной бюджет, стратегия, статусы")
    status, _, text = post("campaigns", {"method": "get", "params": {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "Status", "State", "StatusPayment", "StatusClarification",
                       "DailyBudget", "Funds", "StartDate", "EndDate", "TimeTargeting"],
        "TextCampaignFieldNames": ["BiddingStrategy", "Settings"],
    }})
    if status != 200:
        log("  ✗ HTTP %s — %s" % (status, err_text(text)))
    else:
        camps = json.loads(text)["result"].get("Campaigns", [])
        log("  ✓ кампаний: %d" % len(camps))
        keys = {}
        for c in camps:
            for k in c:
                keys[k] = keys.get(k, 0) + 1
        for k, v in sorted(keys.items()):
            log("     %-24s есть у %d кампаний" % (k, v))
        sample = camps[0] if camps else {}
        if "TextCampaign" in sample:
            log("     BiddingStrategy: %s" % json.dumps(
                sample["TextCampaign"].get("BiddingStrategy", {}), ensure_ascii=False)[:300])
        log("     StatusClarification значения: %s" % list({(c.get("StatusClarification") or "")[:60] for c in camps})[:5])

    log("\n[11] Данные клиента (валюта, ограничения)")
    status, _, text = post("clients", {"method": "get", "params": {
        "FieldNames": ["Login", "Currency", "Restrictions", "AccountQuality", "Type", "Settings"]}})
    if status != 200:
        log("  ✗ HTTP %s — %s" % (status, err_text(text)))
    else:
        cl = json.loads(text)["result"].get("Clients", [{}])[0]
        log("  ✓ поля: %s" % sorted(cl.keys()))
        log("     Restrictions: %s" % json.dumps(cl.get("Restrictions", []), ensure_ascii=False)[:300])

    log("\n[12] Группы объявлений: статусы модерации")
    status, _, text = post("adgroups", {"method": "get", "params": {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "CampaignId", "Status", "Type", "ServingStatus"],
        "Page": {"Limit": 1000}}})
    if status != 200:
        log("  ✗ HTTP %s — %s" % (status, err_text(text)))
    else:
        gs = json.loads(text)["result"].get("AdGroups", [])
        from collections import Counter
        log("  ✓ групп: %d" % len(gs))
        log("     Status: %s" % dict(Counter(g.get("Status") for g in gs)))
        log("     ServingStatus: %s" % dict(Counter(g.get("ServingStatus") for g in gs)))


if __name__ == "__main__":
    main()
