"""Разведка внутреннего API каталога наш.дом.рф.

Открывает каталог и карточку объекта в Chromium (Playwright), перехватывает
все JSON-ответы XHR/fetch и сохраняет их в raw/discovery/ДАТА/.
В консоль выводит список эндпоинтов с параметрами и ключи первого объекта.

    python discover_api.py
"""
import json
import re
from datetime import date
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

from novostroyki import config as C
from novostroyki.nashdom import UA

OUT = C.RAW_DIR / "discovery" / date.today().isoformat()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    seen = []

    def on_response(resp):
        if resp.request.resource_type not in ("xhr", "fetch"):
            return
        if "json" not in (resp.headers.get("content-type") or ""):
            return
        try:
            body = resp.json()
        except Exception:
            return
        n = len(seen) + 1
        u = urlparse(resp.url)
        seen.append({"n": n, "status": resp.status, "path": unquote(u.path),
                     "params": {k: v[0] for k, v in parse_qs(u.query).items()}, "url": resp.url})
        (OUT / f"{n:03d}.json").write_text(
            json.dumps({"url": resp.url, "status": resp.status, "body": body}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=C.CHROMIUM_PATH)
        page = browser.new_context(user_agent=UA, locale="ru-RU").new_page()
        page.on("response", on_response)
        url = f"{C.CATALOG_URL_PUNY}/список-объектов/список?place={C.REGION_CODE}&objStatus=0"
        print("Открываю каталог:", url)
        page.goto(url, wait_until="networkidle", timeout=120000)
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(3000)

        links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        obj = next((l for l in links if re.search(r"/(%D0%BE%D0%B1%D1%8A%D0%B5%D0%BA%D1%82|объект)/\d+", l)), None)
        if obj:
            print("Открываю карточку:", unquote(obj))
            page.goto(obj, wait_until="networkidle", timeout=120000)
            page.wait_for_timeout(3000)
            for tab in ("Проектная декларация", "Документы", "Характеристики"):
                try:
                    page.get_by_text(tab, exact=False).first.click(timeout=3000)
                    page.wait_for_timeout(2500)
                except Exception:
                    pass
        browser.close()

    print(f"\nПерехвачено JSON-ответов: {len(seen)} → {OUT}")
    for s in seen:
        print(f"  #{s['n']:03d} {s['status']} {s['path']}  {s['params']}")
    (OUT / "_index.json").write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
