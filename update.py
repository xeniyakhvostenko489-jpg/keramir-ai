"""Обновление базы новостроек Екатеринбурга в Google Таблице.

    python update.py                 полный цикл fetch → parse → diff → enrich → запись
    python update.py --limit 5       тест на 5 объектах, без записи в таблицу
    python update.py --limit 5 --write   тест на 5 объектах с записью (не для боевой таблицы!)
    python update.py --inspect       показать реальные ключи списка и карточки API
    python update.py --no-enrich     не искать класс для новых ЖК
"""
import argparse
import csv
import json
import sys
import time
from datetime import date, datetime

from novostroyki import config as C
from novostroyki import diff, parse
from novostroyki.nashdom import ApiError, Client, raw_dir_for_today


def log(msg):
    print(msg, flush=True)


def inspect(client):
    js = client.get_json(C.API_LIST, client.list_params(0) | {"limit": 3})
    items, total = client.unpack_list(js)
    log(f"Список: {C.API_LIST}\n  всего: {total}, ключи верхнего уровня ответа: {list(js)}")
    if not items:
        log("  пустой список — проверьте параметры (discover_api.py)")
        return
    log("  ключи объекта: " + ", ".join(sorted(items[0])))
    log(json.dumps(items[0], ensure_ascii=False, indent=1)[:4000])
    oid = parse.pick(items[0], "id")
    card = client.fetch_card(oid)
    log(f"\nКарточка {C.API_OBJECT.format(id=oid)}\n  ключи: " + ", ".join(sorted(card or {})))


def collect(client, limit, errors):
    log("1/5 Выгрузка списка наш.дом.рф…")
    items = client.fetch_list(max_items=limit)
    ekb = [o for o in items if parse.is_ekb(o) and parse.is_building(o)]
    log(f"  в регионе: {len(items)}, из них Екатеринбург/строящиеся: {len(ekb)}")
    if limit:
        ekb = ekb[:limit]

    log("2/5 Карточки объектов (только где не хватает полей)…")
    today = date.today()
    rows, cards = {}, 0
    for o in ekb:
        oid = parse.pick(o, "id")
        if limit or parse.missing_fields(o):
            try:
                o = parse.merge(o, client.fetch_card(oid))
                cards += 1
                if C.API_DECLARATION and parse.missing_fields(o):
                    o = parse.merge(o, client.fetch_declaration(oid))
            except ApiError as e:
                errors.append(f"карточка {oid}: {e}")
        row = parse.to_row(o, today)
        if parse.in_range(row):
            rows[row["ID объекта ЕИСЖС"]] = row
    log(f"  догружено карточек: {cards}; в диапазоне до 4 кв. 2028: {len(rows)}")
    return rows


def gone_status(client, prev, curr, errors):
    """Для выбывших объектов уточняем статус по карточке."""
    out = {}
    for oid in [i for i in prev if i not in curr]:
        try:
            card = client.fetch_card(oid)
            st = parse.pick(card, "status") or ""
            planned = parse.to_date(parse.pick(card, "planned"))
            info = f"нет в выгрузке; статус: {st}" if st else "нет в выгрузке"
            if planned and planned > C.DEADLINE:
                info += f"; срок перенесён на {parse.quarter_label(planned)} (за пределами диапазона)"
            elif not parse.is_ekb(card) and parse.pick(card, "address"):
                info += "; адрес вне Екатеринбурга"
            out[oid] = info
        except ApiError as e:
            errors.append(f"карточка выбывшего {oid}: {e}")
    return out


def method_lines(stats, book_sep):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return [
        ["Раздел", "Описание"],
        ["Последнее обновление", now],
        ["Источник объектов", f"ЕИСЖС, наш.дом.рф — каталог новостроек ({C.CATALOG_URL}); JSON-API: {C.API_LIST}"],
        ["Карточка объекта", C.API_OBJECT],
        ["Фильтры", f"регион {C.REGION_CODE} (Свердловская обл.); адрес содержит «{C.CITY}»; "
                    f"статус — строящиеся (objStatus=0); плановый ввод ≤ 4 кв. 2028 "
                    f"(объекты с прошедшим сроком, но не сданные, включены с пометкой)"],
        ["Класс жилья", "лист «Справочник ЖК». Автоопределение для новых ЖК: erzrf.ru → сайт ЖК/застройщика → "
                        "domclick.ru / cian.ru; при расхождении или отсутствии данных — «не определён». "
                        "Существующие строки справочника скрипт не меняет."],
        ["Изменения", "сравнение по ID ЕИСЖС с предыдущей версией листа «Корпуса»; снапшот — в raw/ДАТА/"],
        ["Перенос срока", "по данным ЕИСЖС, если поле есть; иначе — по истории листа (первый увиденный срок). "
                          "Пусто = данных о переносе нет."],
        ["Статистика запуска", f"объектов: {stats['objects']}; квартир: {stats['flats']}; "
                               f"запросов к API: {stats['requests']}; изменений: {stats['changes']}; "
                               f"новых ЖК: {stats['new_complexes']}; ошибок: {stats['errors']}"],
        ["Ограничения", "1) Фильтр по городу — по тексту адреса ЕИСЖС. 2) Район заполняется, только если он есть в ЕИСЖС. "
                        "3) Класс ищется по открытым страницам и может быть неточным — проверяйте вручную. "
                        "4) Справочник сопоставляется с корпусами по точному названию ЖК. "
                        "5) Если наш.дом.рф изменит API, поправьте FIELDS в novostroyki/parse.py."],
        ["Разделитель формул", book_sep],
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--no-enrich", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    errors = []
    raw = raw_dir_for_today()
    client = Client(raw)

    if args.inspect:
        inspect(client)
        return

    try:
        rows = collect(client, args.limit, errors)
    except ApiError as e:
        log(f"ОШИБКА API наш.дом.рф: {e}\nЗапустите `python discover_api.py`, чтобы проверить адреса API.")
        sys.exit(1)

    write = not args.limit or args.write
    if not write:
        path = raw / "preview.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            wr = csv.writer(fh, delimiter=";")
            wr.writerow(parse.COLUMNS)
            for r in rows.values():
                wr.writerow([r[c] for c in parse.COLUMNS])
        for r in rows.values():
            log(" | ".join(f"{c}: {r[c]}" for c in parse.COLUMNS if r[c] != ""))
        log(f"\nТест: {len(rows)} объектов, предпросмотр: {path}. Таблица не изменялась.")
        return

    from novostroyki.classes import Detector
    from novostroyki.sheets import Book, connect

    log("3/5 Чтение таблицы и сравнение…")
    book = Book(connect())
    prev_values = book.read_korpusa()
    snap = raw / f"snapshot_korpusa_{datetime.now():%H%M%S}.json"
    snap.write_text(json.dumps(prev_values, ensure_ascii=False), encoding="utf-8")
    prev = diff.rows_from_values(prev_values)
    diff.apply_history(rows, prev)
    gone = gone_status(client, prev, rows, errors) if prev else {}
    changes = diff.compute(rows, prev, date.today(), gone)
    new_ids = {c[2] for c in changes if c[1] == diff.T_NEW}

    log("4/5 Справочник ЖК: добавляю только новые ЖК…")
    ref = book.read_reference()
    new_complexes = {}
    for r in rows.values():
        name = r["ЖК"]
        if name and name.lower() not in ref and name.lower() not in new_complexes:
            new_complexes[name.lower()] = r
    detector = Detector()
    ref_rows = []
    for i, r in enumerate(new_complexes.values(), 1):
        if args.no_enrich:
            cls, src = "", ""
        else:
            cls, src = detector.detect(r["ЖК"], r["_complex_site"], r["Сайт застройщика"])
            log(f"  [{i}/{len(new_complexes)}] {r['ЖК']}: {cls} {src}")
        ref_rows.append([r["ЖК"], r["Застройщик (юрлицо)"], cls, r["_complex_site"], src, "нет"])
    errors += detector.errors

    log("5/5 Запись в Google Таблицу…")
    ordered = sorted(rows.values(), key=lambda r: (r["ЖК"].lower(), r["Очередь / корпус"], r["ID объекта ЕИСЖС"]))
    book.append_reference(ref_rows)
    book.write_korpusa(ordered, new_ids if prev else set())
    book.prepend_changes(changes)
    book.write_summaries()

    flats = sum(r["Кол-во квартир"] or 0 for r in rows.values() if isinstance(r["Кол-во квартир"], int))
    stats = {"objects": len(rows), "flats": flats, "requests": client.requests_made,
             "changes": len(changes), "new_complexes": len(ref_rows), "errors": len(errors)}
    book.write_method(method_lines(stats, book.sep))
    (raw / "errors.log").write_text("\n".join(errors), encoding="utf-8")

    unchecked = sum(1 for r in ref_rows if r[2] in ("", C.CLASS_UNKNOWN))
    log("\n===== ОТЧЁТ =====")
    log(f"Объектов: {len(rows)}, квартир: {flats}")
    for t, n in diff.count_by_type(changes).items():
        log(f"  {t}: {n}")
    log(f"Новых ЖК в справочнике: {len(ref_rows)} (класс не определён: {unchecked}; "
        f"все новые требуют ручной проверки)")
    log(f"Ошибок: {len(errors)}" + (f" — см. {raw / 'errors.log'}" if errors else ""))
    for e in errors[:10]:
        log(f"  - {e}")
    log(f"Время: {time.time() - t0:.0f} с. Таблица: https://docs.google.com/spreadsheets/d/{C.SPREADSHEET_ID}")


if __name__ == "__main__":
    main()
