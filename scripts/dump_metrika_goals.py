#!/usr/bin/env python3
"""One-off diagnostic: list every goal defined on a Metrika counter, by id, name
and type, straight from the Management API — no guessing from Direct's strategy
data. Written to answer directly "what is goal 13" instead of inferring it.

Not part of the regular dashboard pipeline. Writes <DATA_DIR>/metrika_goals_dump.json.

Environment: METRIKA_TOKEN (required), DATA_DIR, COUNTER_ID (defaults to 10128436,
             the KeraMir counter).
"""
import json
import os
import urllib.error
import urllib.request

DATA_DIR = os.environ.get("DATA_DIR", "data").rstrip("/")
OUT = DATA_DIR + "/metrika_goals_dump.json"
COUNTER_ID = os.environ.get("COUNTER_ID", "").strip() or "10128436"


def log(*a):
    print(*a, flush=True)


def token():
    t = os.environ.get("METRIKA_TOKEN", "").strip()
    if not t:
        raise SystemExit("METRIKA_TOKEN is not set")
    return t


def call(url):
    req = urllib.request.Request(url, headers={"Authorization": "OAuth " + token()})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError("Metrika API %s: HTTP %s %s" % (url, e.code, body[:800]))


def main():
    url = "https://api-metrika.yandex.net/management/v1/counter/%s/goals" % COUNTER_ID
    data = call(url)
    goals = data.get("goals", [])
    log("Счётчик %s: целей всего %d" % (COUNTER_ID, len(goals)))
    for g in sorted(goals, key=lambda g: g.get("id", 0)):
        log("  id=%s  type=%s  name=%r" % (g.get("id"), g.get("type"), g.get("name")))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"counter_id": COUNTER_ID, "goals": goals}, f, ensure_ascii=False, indent=1)
    log("written:", OUT)


if __name__ == "__main__":
    main()
