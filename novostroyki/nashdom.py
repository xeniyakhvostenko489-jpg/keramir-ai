"""Клиент JSON-API каталога новостроек наш.дом.рф (ЕИСЖС)."""
import json
import random
import time
from datetime import date
from pathlib import Path

import requests

from . import config as C

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class ApiError(Exception):
    pass


def browser_cookies(url):
    """Открывает страницу в Chromium и возвращает cookies (обход анти-бот заглушки)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale="ru-RU")
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=90000)
        cookies = ctx.cookies()
        browser.close()
    return cookies


class Client:
    def __init__(self, raw_dir: Path):
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": C.CATALOG_URL_PUNY,
        })
        self._bootstrapped = False
        self.requests_made = 0

    def _bootstrap(self):
        """Один раз подтягивает cookies через браузер, если API отвечает 401/403."""
        if self._bootstrapped:
            return False
        self._bootstrapped = True
        try:
            for c in browser_cookies(C.CATALOG_URL_PUNY):
                self.s.cookies.set(c["name"], c["value"], domain=c.get("domain"))
            return True
        except Exception as e:  # playwright не установлен / нет браузера
            print(f"  ! не удалось получить cookies через браузер: {e}")
            return False

    def get_json(self, url, params=None):
        last = None
        for attempt in range(1, C.RETRIES + 1):
            time.sleep(random.uniform(*C.PAUSE))
            try:
                r = self.s.get(url, params=params, timeout=60)
                self.requests_made += 1
                if r.status_code in (401, 403) and self._bootstrap():
                    continue
                if r.status_code == 404:
                    raise ApiError(f"404 {r.url}")
                r.raise_for_status()
                return r.json()
            except ApiError:
                raise
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(min(2 ** attempt, 30))
        raise ApiError(f"{url}: {last}")

    def _save(self, name, payload):
        path = self.raw_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def unpack_list(js):
        data = js.get("data", js) if isinstance(js, dict) else js
        if isinstance(data, list):
            return data, None
        items = data.get("list") or data.get("items") or data.get("content") or []
        total = data.get("total") or data.get("totalElements") or data.get("count")
        return items, total

    def list_params(self, offset):
        p = {
            "offset": offset,
            "limit": C.PAGE_SIZE,
            "sortField": "devId.devShortCleanNm",
            "sortType": "asc",
            "objStatus": 0,          # строящиеся
            "place": C.REGION_CODE,  # Свердловская область
        }
        p.update(C.EXTRA_PARAMS)
        return p

    def fetch_list(self, max_items=None):
        """Все страницы списка. Каждая страница сохраняется в raw как есть."""
        out, offset, page = [], 0, 1
        while True:
            js = self.get_json(C.API_LIST, self.list_params(offset))
            self._save(f"list_page_{page:04d}.json", js)
            items, total = self.unpack_list(js)
            out.extend(items)
            print(f"  страница {page}: {len(items)} шт. (всего в API: {total})")
            offset += C.PAGE_SIZE
            page += 1
            if not items or (total is not None and offset >= int(total)):
                break
            if max_items and len(out) >= max_items * 20:  # тестовый режим: не листаем весь регион
                break
        return out

    def fetch_card(self, obj_id):
        js = self.get_json(C.API_OBJECT.format(id=obj_id))
        self._save(f"cards/{obj_id}.json", js)
        return js.get("data", js) if isinstance(js, dict) else js

    def fetch_declaration(self, obj_id):
        if not C.API_DECLARATION:
            return None
        js = self.get_json(C.API_DECLARATION.format(id=obj_id))
        self._save(f"declarations/{obj_id}.json", js)
        return js.get("data", js) if isinstance(js, dict) else js


def raw_dir_for_today():
    return C.RAW_DIR / date.today().isoformat()
