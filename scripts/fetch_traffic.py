#!/usr/bin/env python3
"""Платный (реклама) vs органический и остальной трафик сайта — по данным Метрики.

Остальной дашборд видит только собственные клики Директа, то есть только платный
трафик. Этот скрипт обращается напрямую к Yandex Metrika Reporting API (Stat API)
и делит ВЕСЬ трафик сайта по источникам — реклама/органика/прямые заходы/переходы/
соцсети — по визитам и по достижениям отслеживаемых целей (тот же список целей,
что и на вкладке «Цели», см. fetch_goals.py).

Это отдельный API со своими правами доступа: OAuth-токен Директа их не даёт (даже
если это тот же аккаунт) — нужен токен с разрешением «Яндекс.Метрика: чтение данных»,
и этот аккаунт должен иметь доступ на просмотр нужных счётчиков в самой Метрике.
Счётчики берутся из data/config.json.metrika_counters; если список пуст, скрипт
запрашивает все счётчики, которые видны токену (это может включать старые или
тестовые счётчики, поэтому лучше явно перечислить нужные).

Пишет data/traffic.json: список источников и разреженная таблица
[индекс источника, дата, визиты, достижения целей].

Environment: METRIKA_TOKEN (обязательно), DASH_KEY, DAYS (по умолчанию 400),
             TRAFFIC_MAX_AGE_HOURS (по умолчанию 20), FORCE_TRAFFIC=1.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direct_api as api
from direct_api import log
from fetch_goals import tracked_goals

OUT = "data/traffic.json"
MGMT_URL = "https://api-metrika.yandex.net/management/v1/counters"
STAT_URL = "https://api-metrika.yandex.net/stat/v1/data"
GOALS_PER_CALL = 10   # как и в Директе, разумный предел числа целей в одном запросе

SOURCE_RU = {
    "ad": "Реклама", "organic": "Органика (поиск)", "direct": "Прямые заходы",
    "referral": "Переходы с сайтов", "social": "Соцсети", "internal": "Внутренние переходы",
    "recommend": "Рекомендательные системы",
}
SOURCE_ORDER = ["ad", "organic", "direct", "referral", "social", "recommend", "internal"]


def token():
    t = os.environ.get("METRIKA_TOKEN", "").strip()
    if not t:
        raise SystemExit("METRIKA_TOKEN is not set")
    return t


def call(url, params):
    q = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url + "?" + q, headers={"Authorization": "OAuth " + token()})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < 5:
                wait = 3 * (attempt + 1)
                log("  Метрика: 429, ждём %d с" % wait)
                time.sleep(wait)
                continue
            raise RuntimeError("Metrika API %s: HTTP %s %s" % (url, e.code, body[:400]))
    raise RuntimeError("Metrika API %s: не дождались ответа" % url)


def counters(cfg):
    """data/config.json.metrika_counters, если задан, иначе все счётчики, видные токену
    (у него может быть доступ к старым/тестовым счётчикам, которые не нужны в отчёте)."""
    configured = [int(c) for c in (cfg.get("metrika_counters") or [])]
    if configured:
        log("  счётчики заданы в config.json: %s" % ", ".join(str(c) for c in configured))
        return configured
    data = call(MGMT_URL, {"per_page": 100})
    out = [c["id"] for c in data.get("counters", [])]
    log("  счётчиков доступно токену: %d (%s)" % (len(out), ", ".join(str(c) for c in out)))
    return out


def load_config():
    try:
        with open("data/config.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def stat(counter_id, metrics, date_from, date_to):
    """Один запрос Stat API: дата x источник трафика -> список метрик."""
    data = call(STAT_URL, {
        "ids": counter_id,
        "metrics": ",".join(metrics),
        "dimensions": "ym:s:date,ym:s:lastTrafficSource",
        "date1": date_from, "date2": date_to,
        "accuracy": "full", "limit": 100000,
    })
    return data.get("data", [])


def merge_rows(rows, out, col_offset, n_cols):
    """rows из Stat API -> out[(date, source)][col] += value."""
    for row in rows:
        dims = row.get("dimensions", [])
        date = dims[0].get("name") if len(dims) > 0 else None
        source = (dims[1].get("id") if len(dims) > 1 else None) or "undefined"
        if not date:
            continue
        vals = row.get("metrics", [])
        key = (date, source)
        acc = out[key]
        for i in range(n_cols):
            v = vals[i] if i < len(vals) else 0
            acc[col_offset + i] = (acc[col_offset + i] or 0) + (v or 0)


def fresh_enough():
    if os.environ.get("FORCE_TRAFFIC") == "1":
        return False
    try:
        with open(OUT, encoding="utf-8") as f:
            gen = json.load(f)["meta"]["generated_at"]
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(gen.replace("Z", "+00:00"))
        return age < dt.timedelta(hours=float(os.environ.get("TRAFFIC_MAX_AGE_HOURS", "20")))
    except Exception:
        return False


def main():
    token()
    if fresh_enough():
        log("traffic.json свежий, пропускаем")
        return
    cfg = load_config()
    try:
        cids = counters(cfg)
    except Exception as e:
        log("  не удалось получить список счётчиков: %s" % e)
        return
    if not cids:
        log("  ни одного счётчика Метрики, файл не пишем")
        return

    goal_ids = [g["id"] for g in tracked_goals(cfg)]

    days = int(os.environ.get("DAYS", "400"))
    to = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    frm = to - dt.timedelta(days=days - 1)
    date_from, date_to = frm.isoformat(), to.isoformat()
    log("Трафик по источникам за %s .. %s, целей: %d" % (date_from, date_to, len(goal_ids)))

    acc = defaultdict(lambda: [0, 0])   # (date, source) -> [visits, goal_reaches]
    failed = []
    for cid in cids:
        try:
            rows = stat(cid, ["ym:s:visits"], date_from, date_to)
            merge_rows(rows, acc, 0, 1)
        except Exception as e:
            log("  счётчик %s: визиты не получены (%s)" % (cid, e))
            failed.append(str(cid))
            continue
        if not goal_ids:
            continue
        for i in range(0, len(goal_ids), GOALS_PER_CALL):
            chunk = goal_ids[i:i + GOALS_PER_CALL]
            metrics = ["ym:s:goal%sreaches" % g for g in chunk]
            try:
                rows = stat(cid, metrics, date_from, date_to)
            except Exception as e:
                log("  счётчик %s: цели (часть %d) не получены (%s)" % (cid, i // GOALS_PER_CALL, e))
                continue
            for row in rows:
                dims = row.get("dimensions", [])
                date = dims[0].get("name") if len(dims) > 0 else None
                source = (dims[1].get("id") if len(dims) > 1 else None) or "undefined"
                if not date:
                    continue
                total = sum(v or 0 for v in row.get("metrics", []))
                key = (date, source)
                acc[key][1] += total

    table = []
    for (date, source), (visits, goals_v) in acc.items():
        if not visits and not goals_v:
            continue
        idx = SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER)
        table.append([idx, date, source, int(round(visits)), int(round(goals_v))])
    table.sort(key=lambda r: (r[1], r[0]))

    sources = [{"id": s, "name": SOURCE_RU.get(s, s)} for s in SOURCE_ORDER]
    meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                          .isoformat().replace("+00:00", "Z"),
        "from": date_from, "to": date_to,
        "counters": cids, "counters_failed": failed,
        "goals": len(goal_ids), "rows": len(table),
        "encrypted": bool(os.environ.get("DASH_KEY", "").strip()),
    }
    api.write_data(OUT, {"sources": sources, "daily": table}, meta)
    log("Готово: строк %d, счётчиков %d (ошибок %d)" % (len(table), len(cids), len(failed)))


if __name__ == "__main__":
    main()
