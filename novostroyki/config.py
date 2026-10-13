"""Настройки. Секреты (ID таблицы, ключ) — только в config/.env."""
import json
import os
from datetime import date
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
GOOGLE_KEY_PATH = os.getenv("GOOGLE_KEY_PATH", "config/service_account.json").strip()
GOOGLE_SA_JSON = os.getenv("GOOGLE_SA_JSON", "").strip()

# наш.дом.рф в punycode; путь /сервисы/ — в percent-encoding
HOST = "https://xn--80az8a.xn--d1aqf.xn--p1ai"
SVC = HOST + "/" + quote("сервисы")
API_LIST = os.getenv("NASHDOM_API_LIST") or SVC + "/api/kn/object"
API_OBJECT = os.getenv("NASHDOM_API_OBJECT") or SVC + "/api/object/{id}"
API_DECLARATION = os.getenv("NASHDOM_API_DECLARATION", "")  # пусто — не грузим
EXTRA_PARAMS = json.loads(os.getenv("NASHDOM_EXTRA_PARAMS") or "{}")

CATALOG_URL = "https://наш.дом.рф/сервисы/каталог-новостроек"
CATALOG_URL_PUNY = SVC + "/" + quote("каталог-новостроек")
OBJECT_URL = "https://наш.дом.рф/сервисы/каталог-новостроек/объект/{id}"

REGION_CODE = "66"            # Свердловская область
CITY = "Екатеринбург"
DEADLINE = date(2028, 12, 31)  # плановый ввод ≤ 4 кв. 2028
PAGE_SIZE = 100
PAUSE = (0.5, 1.0)             # пауза между запросами, сек
RETRIES = 5

RAW_DIR = ROOT / "raw"

CLASS_VALUES = ["эконом", "комфорт", "комфорт+", "бизнес", "премиум"]
CLASS_UNKNOWN = "не определён"
