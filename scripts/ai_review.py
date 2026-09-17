#!/usr/bin/env python3
"""Ask Claude for a short review of every campaign and of the account as a whole.

Reads data/direct.json (written by fetch_direct.py, plain or encrypted with DASH_KEY),
aggregates the last 30 days vs the previous 30 days per campaign, sends one request to
Claude and writes data/ai.json (encrypted the same way when DASH_KEY is set).

Environment:
  ANTHROPIC_API_KEY - required
  DASH_KEY          - passphrase used by fetch_direct.py (optional)
  AI_MODEL          - model id, default claude-opus-5
  AI_MAX_AGE_HOURS  - skip when the existing review is younger than this (default 20)
  FORCE_AI=1        - ignore AI_MAX_AGE_HOURS

  --dry-run         - build the prompt payload and print it, do not call the API
"""
import base64
import datetime as dt
import hashlib
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_direct import encrypt  # noqa: E402

DATA = "data/direct.json"
OUT = "data/ai.json"
MODEL = os.environ.get("AI_MODEL", "claude-opus-5")

SYSTEM = """Ты performance-маркетолог розничной сети КераМир (плитка, керамогранит, сантехника, мебель для ванной; магазины в Екатеринбурге, Уфе, Тюмени, Челябинске, Перми). Анализируешь Яндекс Директ по данным Reports API.
Правила:
- Поиск и РСЯ оцениваешь раздельно; тип кампании определяй по полю net_split, а не по названию.
- «Лиды» (leads) = достижения только тех целей, которые бизнес считает заявкой: формы, заказ звонка, пройденный квиз. Их список в lead_goals. «Выполненные цели» (goal_completions) = достижения всех целей Метрики, включая поведенческие; это не заявки, на них не опирайся при оценке эффективности.
- CR и CPL считаются от лидов, а не от выполненных целей. Лидов на порядок меньше, чем выполненных целей, — это нормально.
- Данных о заявках и выручке из 1С нет: не делай выводов о продажах.
- Пиши по-русски, коротко, с конкретными числами из данных. Никаких общих фраз вроде «продолжайте оптимизировать».
- verdict: good — лиды растут или CPL ниже среднего при заметном бюджете; ok — норма; warn — CPL заметно выше среднего, падение лидов/CTR, рост CPC; bad — расход без лидов или резкое ухудшение.
- advice: 1–3 конкретных действия, каждое одной фразой (что именно изменить и почему).
- Опирайся на настройки кампаний из блока settings: стратегия, дневной бюджет, недельный лимит, статус. Если кампания упирается в лимит или у неё отключены показы в сетях, это объясняет цифры — назови это прямо.
- Блоки keywords, queries, geo, placements и audience содержат срезы за 30 дней. Используй их для конкретики: называй фразы, запросы, регионы и площадки по именам.
- Объём трафика (traffic_volume) — доля кликов, которую забирает позиция объявления: 100 это верх выдачи. Низкое значение при заметных показах означает, что ставка мала."""

SCHEMA = {
    "type": "object",
    "properties": {
        "overall": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "2–4 предложения: общее состояние аккаунта за 30 дней против предыдущих 30"},
                "highlights": {"type": "array", "items": {"type": "string"}, "description": "3–6 главных наблюдений с числами"},
                "actions": {"type": "array", "items": {"type": "string"}, "description": "3–5 приоритетных действий, от самого важного"},
            },
            "required": ["summary", "highlights", "actions"],
            "additionalProperties": False,
        },
        "campaigns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cid": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["good", "ok", "warn", "bad"]},
                    "summary": {"type": "string", "description": "1–2 предложения о состоянии кампании"},
                    "advice": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["cid", "verdict", "summary", "advice"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overall", "campaigns"],
    "additionalProperties": False,
}


def log(*a):
    print(*a, flush=True)


def decrypt(file, passphrase):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv, ct = (base64.b64decode(file[k]) for k in ("salt", "iv", "ct"))
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 100000, 32)
    return json.loads(AESGCM(key).decrypt(iv, ct, None).decode("utf-8"))


def load_rows():
    with open(DATA, encoding="utf-8") as f:
        file = json.load(f)
    if "enc" in file:
        key = os.environ.get("DASH_KEY", "")
        if not key:
            raise SystemExit("data/direct.json is encrypted but DASH_KEY is not set")
        file = decrypt(file, key)
    return file["meta"], file["rows"]


def agg(rows):
    a = {"impr": 0, "clicks": 0, "cost": 0.0, "sessions": None, "bounces": None,
         "conv": None, "leads": None}
    for r in rows:
        a["impr"] += r[6]; a["clicks"] += r[7]; a["cost"] += r[8]
        if r[9] is not None: a["sessions"] = (a["sessions"] or 0) + r[9]
        if r[10] is not None: a["bounces"] = (a["bounces"] or 0) + r[10]
        if r[11] is not None: a["conv"] = (a["conv"] or 0) + r[11]
        if len(r) > 12 and r[12] is not None: a["leads"] = (a["leads"] or 0) + r[12]
    d = lambda x, y: round(x / y, 4) if y else None
    return {
        "cost": round(a["cost"]), "impr": a["impr"], "clicks": a["clicks"],
        "ctr": d(a["clicks"], a["impr"]), "cpc": d(a["cost"], a["clicks"]),
        "sessions": a["sessions"], "bounce_rate": d(a["bounces"] or 0, a["sessions"]) if a["sessions"] else None,
        "goal_completions": a["conv"],
        "leads": a["leads"], "cr": d(a["leads"], a["clicks"]) if a["leads"] is not None else None,
        "cpl": round(a["cost"] / a["leads"]) if a["leads"] else None,
    }


def load_slice(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None
    if "enc" in d:
        key = os.environ.get("DASH_KEY", "")
        if not key:
            return None
        d = decrypt(d, key)
    return d


def extra_context():
    """Настройки кампаний и срезы за 30 дней — чтобы советы опирались на них."""
    ctx = {}
    sl = load_slice("data/slices.json")
    if sl:
        ctx["settings"] = [{
            "cid": c["id"], "name": c["name"], "state": c.get("state"),
            "status": c.get("clarification"), "payment": c.get("payment"),
            "strategy_search": c.get("strategySearchRu"), "strategy_network": c.get("strategyNetworkRu"),
            "daily_budget": c.get("dailyBudget"), "limits": c.get("strategyLimits"),
        } for c in sl.get("campaigns", []) if c.get("state") != "ARCHIVED"]
        geo = (sl.get("geoPresence") or {}).get("w30") or []
        targ = [r[0] for r in (sl.get("geoTargeting") or {}).get("w30") or []]
        out = [r for r in geo if not any(r[0] == t or t in r[0] or r[0] in t for t in targ)]
        ctx["geo"] = {
            "top_regions": [{"region": r[0], "cost": r[3], "leads": r[4]} for r in geo[:10]],
            "targeting_regions": targ[:20],
            "cost_outside_targeting": round(sum(r[3] for r in out)),
            "cost_total": round(sum(r[3] for r in geo)),
        }
        pl = [r for r in (sl.get("placements") or {}).get("w30") or [] if r[1] == "AD_NETWORK"]
        ctx["placements"] = [{"placement": r[0], "cost": r[4], "leads": r[5]}
                             for r in sorted(pl, key=lambda r: -r[4])[:10]]
        ctx["audience"] = [{"gender": r[0], "age": r[1], "cost": r[4], "leads": r[5]}
                           for r in ((sl.get("demo") or {}).get("w30") or [])]
        groups = {}
        for r in sl.get("adWeeks", []):
            groups.setdefault(r[1], set()).add(r[0])
        if groups:
            ctx["ads"] = {"groups": len(groups),
                          "groups_with_one_ad": sum(1 for v in groups.values() if len(v) == 1)}
    kw = load_slice("data/keywords.json")
    if kw:
        rows = kw.get("w30") or []
        top = sorted(rows, key=lambda r: -r[7])[:40]
        low_tv = sorted([r for r in rows if r[9] is not None and r[5] >= 100], key=lambda r: r[9])[:15]
        fmt = lambda r: {"keyword": r[1], "cid": r[2], "impressions": r[5], "clicks": r[6],
                         "cost": r[7], "leads": r[8], "traffic_volume": r[9]}
        ctx["keywords"] = {"top_by_cost": [fmt(r) for r in top],
                           "lowest_traffic_volume": [fmt(r) for r in low_tv]}
    q = load_slice("data/queries.json")
    if q:
        rows = q.get("w30") or []
        waste = sorted([r for r in rows if (r[7] or 0) == 0 and r[6] > 0], key=lambda r: -r[6])[:40]
        ctx["queries"] = {"top_cost_no_leads": [{"query": r[0], "cid": r[1], "clicks": r[5], "cost": r[6]}
                                                for r in waste],
                          "total_cost_no_leads": round(sum(r[6] for r in rows if (r[7] or 0) == 0))}
    return ctx


def build_payload(meta, rows):
    to = dt.date.fromisoformat(meta["to"])
    cur_from = to - dt.timedelta(days=29)
    prev_to = cur_from - dt.timedelta(days=1)
    prev_from = prev_to - dt.timedelta(days=29)
    last7 = to - dt.timedelta(days=6)
    iso = lambda d: d.isoformat()
    cur = [r for r in rows if iso(cur_from) <= r[0] <= iso(to)]
    prev = [r for r in rows if iso(prev_from) <= r[0] <= iso(prev_to)]
    by_cur, by_prev, names = defaultdict(list), defaultdict(list), {}
    for r in cur: by_cur[r[1]].append(r); names[r[1]] = r[2]
    for r in prev: by_prev[r[1]].append(r); names.setdefault(r[1], r[2])
    total = agg(cur)
    camps = []
    for cid, rs in sorted(by_cur.items(), key=lambda kv: -sum(r[8] for r in kv[1])):
        a = agg(rs)
        search_cost = sum(r[8] for r in rs if r[4] == "SEARCH")
        mobile_clicks = sum(r[7] for r in rs if r[5] == "MOBILE")
        l7 = agg([r for r in rs if r[0] >= iso(last7)])
        days_with_clicks = len({r[0] for r in rs if r[7] > 0})
        camps.append({
            "cid": cid, "name": names[cid],
            "share_of_spend": round(a["cost"] / total["cost"], 3) if total["cost"] else 0,
            "net_split": {"search": round(search_cost / a["cost"], 2) if a["cost"] else None},
            "mobile_share_clicks": round(mobile_clicks / a["clicks"], 2) if a["clicks"] else None,
            "days_with_clicks_of_30": days_with_clicks,
            "last_30d": a, "prev_30d": agg(by_prev.get(cid, [])), "last_7d": l7,
        })
    return {
        "period": {"current": [iso(cur_from), iso(to)], "previous": [iso(prev_from), iso(prev_to)]},
        "goals_configured": bool(meta.get("goals")),
        "lead_goals": meta.get("lead_goals") or [],
        "totals": {"last_30d": total, "prev_30d": agg(prev)},
        "by_network_last_30d": {
            "search": agg([r for r in cur if r[4] == "SEARCH"]),
            "network": agg([r for r in cur if r[4] == "AD_NETWORK"]),
        },
        "campaigns": camps,
    }


def fresh_enough():
    try:
        with open(OUT, encoding="utf-8") as f:
            gen = json.load(f)["meta"]["generated_at"]
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(gen.replace("Z", "+00:00"))
        return age < dt.timedelta(hours=float(os.environ.get("AI_MAX_AGE_HOURS", "20")))
    except Exception:
        return False


def main():
    dry = "--dry-run" in sys.argv
    if not dry and os.environ.get("FORCE_AI") != "1" and fresh_enough():
        log("ai.json is fresh, skipping"); return
    meta, rows = load_rows()
    payload = build_payload(meta, rows)
    payload.update(extra_context())
    user_msg = ("Данные Яндекс Директ КераМир (JSON). Сделай обзор аккаунта и каждой кампании из списка campaigns "
                "(верни запись для каждого cid).\n\n" + json.dumps(payload, ensure_ascii=False))
    if dry:
        log(json.dumps(payload, ensure_ascii=False, indent=1)[:4000]); log("... payload chars:", len(user_msg)); return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    import anthropic
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.RateLimitError as e:
        raise SystemExit("rate limited: %s" % e.message)
    except anthropic.APIStatusError as e:
        raise SystemExit("API error %s: %s" % (e.status_code, e.message))
    except anthropic.APIConnectionError as e:
        raise SystemExit("network error: %s" % e)
    if response.stop_reason == "refusal":
        raise SystemExit("model refused: %s" % (response.stop_details.explanation if response.stop_details else ""))
    text = "".join(b.text for b in response.content if b.type == "text")
    review = json.loads(text)
    by_cid = {c["cid"]: c for c in review["campaigns"]}
    out_meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "model": response.model, "period": payload["period"],
        "campaigns": len(by_cid), "encrypted": bool(os.environ.get("DASH_KEY", "").strip()),
        "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
    }
    body = {"meta": out_meta, "overall": review["overall"], "campaigns": by_cid}
    key = os.environ.get("DASH_KEY", "").strip()
    if key:
        out = encrypt(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), key)
        out["meta"] = out_meta
    else:
        out = body
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log("done:", json.dumps(out_meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
