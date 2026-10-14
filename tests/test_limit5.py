"""Офлайн-тест `update.py --limit 5`: HTTP подменён синтетическими ответами
в формате ЕИСЖС (данные выдуманы, проверяется только логика конвейера).

    python -m pytest tests/ -q
"""
import csv
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import update  # noqa: E402
from novostroyki import config as C, nashdom, parse  # noqa: E402


def obj(i, addr, ready, status=0, **kw):
    o = {"objId": 1000 + i, "objAddr": addr, "objReady100PercDt": ready, "objStatus": status,
         "developer": {"devShortCleanNm": f"СЗ Тест-{i}"}}
    o.update(kw)
    return o


LIST = [
    obj(1, "г Екатеринбург, ул Тестовая, корпус 1", "2026-12-31",
        objCommercNm="ЖК Альфа", objElemLivingCnt=120, objSquareLiving=6000.5),
    obj(2, "г Екатеринбург, ул Тестовая, корпус 2", "2027-06-30", objCommercNm="ЖК Альфа"),
    obj(3, "г Екатеринбург, ул Другая, 5", "2029-03-31", objCommercNm="ЖК Поздний"),   # вне срока
    obj(4, "г Березовский, ул Лесная, 1", "2026-09-30", objCommercNm="ЖК Сосед"),      # не ЕКБ
    obj(5, "г Екатеринбург, ул Сданная, 7", "2025-12-31", status=1),                  # сдан
    obj(6, "г Екатеринбург, ул Новая, 3", "2028-12-31", objCommercNm="ЖК Бета", objElemLivingCnt=300),
    obj(7, "г Екатеринбург, ул Новая, 4", None, objCommercNm="ЖК Бета"),              # нет срока
    obj(8, "г Екатеринбург, ул Новая, 5", "2025-03-31", objCommercNm="ЖК Бета"),      # срок прошёл
]
CARDS = {
    1002: {"objElemLivingCnt": 80, "developer": {"devInn": "6600000002"}},
    1006: {"objFloorMin": 9, "objFloorMax": 25},
}


@pytest.fixture
def fake_api(monkeypatch, tmp_path):
    def get_json(self, url, params=None):
        self.requests_made += 1
        if url == C.API_LIST:
            off = params["offset"]
            return {"data": {"list": LIST[off:off + C.PAGE_SIZE], "total": len(LIST)}}
        oid = int(url.rstrip("/").rsplit("/", 1)[-1])
        return {"data": CARDS.get(oid, {})}

    monkeypatch.setattr(nashdom.Client, "get_json", get_json)
    monkeypatch.setattr(update, "raw_dir_for_today", lambda: tmp_path)
    return tmp_path


def test_limit5_preview(fake_api, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["update.py", "--limit", "5"])
    update.main()

    with (fake_api / "preview.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    by_id = {r["ID объекта ЕИСЖС"]: r for r in rows}

    # 5 первых объектов ЕКБ в статусе «строится»: 1001, 1002, 1003, 1006, 1007;
    # 1003 (2029) отсекается фильтром срока → 4 строки
    assert set(by_id) == {"1001", "1002", "1006", "1007"}
    assert by_id["1001"]["Плановый ввод (квартал)"] == "2026, 4 кв."
    assert by_id["1001"]["Кол-во квартир"] == "120"
    assert by_id["1001"]["Очередь / корпус"] == "корпус 1"
    assert by_id["1002"]["Кол-во квартир"] == "80"          # догружено из карточки
    assert by_id["1002"]["ИНН"] == "6600000002"
    assert by_id["1006"]["Этажность"] == "9–25"
    assert "нет планового срока" in by_id["1007"]["Комментарий"]
    assert "Таблица не изменялась" in capsys.readouterr().out


def test_to_date_formats():
    assert parse.to_date("31.12.2027") == date(2027, 12, 31)
    assert parse.to_date("2028-03-31T00:00:00") == date(2028, 3, 31)
    assert parse.to_date("4 кв. 2026") == date(2026, 12, 28)
    assert parse.to_date(1798675200000) == date(2026, 12, 31)
