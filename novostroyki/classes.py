"""Автоопределение класса жилья для новых ЖК.

Порядок: erzrf.ru → сайт ЖК/застройщика → domclick.ru / cian.ru.
Страницы на erzrf/domclick/cian ищутся поиском по сайту (DuckDuckGo HTML).
Класс засчитывается, только если страница упоминает название ЖК и на ней
найден ровно один класс. Источники расходятся или пусто → «не определён».
"""
import random
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from . import config as C
from .nashdom import UA

CLS = r"(эконом|стандарт|комфорт\s*(?:\+|плюс|-\s*плюс)?|бизнес\s*(?:\+|плюс)?|премиум|элитн\w*|де[\s-]?люкс)"
PATTERNS = [
    re.compile(r"класс(?:\s+жилья|\s+жк|\s+объекта|\s+дома)?\s*[:—–\-]?\s*" + CLS, re.I),
    re.compile(CLS + r"[\s\-–]*класс", re.I),
]


def normalize(text):
    t = re.sub(r"\s+", "", str(text).lower())
    if t.startswith(("элит", "делюкс", "де-люкс", "премиум")):
        return "премиум"
    if t.startswith("бизнес"):
        return "бизнес"
    if t.startswith("комфорт"):
        return "комфорт+" if ("+" in t or "плюс" in t) else "комфорт"
    if t.startswith(("эконом", "стандарт")):
        return "эконом"
    return None


def clean_name(name):
    n = re.sub(r"^\s*(жк|жилой комплекс|жилой квартал|квартал)\s+", "", name, flags=re.I)
    return n.strip(" «»\"'")


class Detector:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
        self.errors = []

    def _get(self, url):
        time.sleep(random.uniform(*C.PAUSE))
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code == 200:
                    return r.text
                if r.status_code in (403, 404, 451):
                    return None
            except requests.RequestException:
                pass
            time.sleep(2 ** attempt)
        return None

    def search(self, site, query):
        html = self._get("https://html.duckduckgo.com/html/?q=" + quote_plus(f"site:{site} {query}"))
        if not html:
            return []
        urls = []
        for a in BeautifulSoup(html, "html.parser").select("a.result__a"):
            href = a.get("href", "")
            if "uddg=" in href:
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
            if site in urlparse(href).netloc:
                urls.append(href)
        return urls[:3]

    def classes_on(self, url, name):
        """Множество классов на странице, если страница про этот ЖК."""
        html = self._get(url)
        if not html:
            return set()
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        if name and clean_name(name).lower() not in text.lower():
            return set()
        found = set()
        for pat in PATTERNS:
            for m in pat.finditer(text):
                c = normalize(m.group(1))
                if c:
                    found.add(c)
        return found

    def from_site(self, site, name):
        for url in self.search(site, f"ЖК {clean_name(name)} Екатеринбург"):
            found = self.classes_on(url, name)
            if len(found) == 1:
                return found.pop(), url
            if len(found) > 1:
                return None, url  # на странице несколько классов — не берём
        return None, None

    def detect(self, name, complex_site="", dev_site=""):
        """→ (класс, источник). Класс — одно из CLASS_VALUES или «не определён»."""
        if not name:
            return C.CLASS_UNKNOWN, ""
        try:
            erz, erz_url = self.from_site("erzrf.ru", name)

            own, own_url = None, None
            for url in [u for u in (complex_site, dev_site) if u]:
                found = self.classes_on(url, name)
                if len(found) == 1:
                    own, own_url = found.pop(), url
                    break

            if erz and own and erz != own:
                return C.CLASS_UNKNOWN, f"расхождение: erzrf={erz}, сайт={own}"
            if erz:
                return erz, erz_url
            if own:
                return own, own_url

            dom, dom_url = self.from_site("domclick.ru", name)
            cian, cian_url = self.from_site("cian.ru", name)
            if dom and cian and dom != cian:
                return C.CLASS_UNKNOWN, f"расхождение: domclick={dom}, cian={cian}"
            if dom:
                return dom, dom_url
            if cian:
                return cian, cian_url
        except Exception as e:
            self.errors.append(f"класс «{name}»: {e}")
        return C.CLASS_UNKNOWN, ""
