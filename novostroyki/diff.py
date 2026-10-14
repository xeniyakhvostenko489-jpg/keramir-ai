"""Сравнение новой выгрузки с предыдущей версией листа «Корпуса» по ID ЕИСЖС."""
from .parse import COLUMNS

CHANGE_COLUMNS = ["Дата обновления", "Тип изменения", "ID объекта", "ЖК", "Корпус",
                  "Застройщик", "Было", "Стало", "Ссылка"]

T_NEW = "новый объект"
T_MOVE = "перенос срока"
T_GONE = "сдан / выбыл"
T_FLATS = "изменилось кол-во квартир"
T_DEV = "сменился застройщик"
T_INIT = "первичная загрузка"
TYPES = [T_NEW, T_MOVE, T_GONE, T_FLATS, T_DEV]


def rows_from_values(values):
    """Значения листа (список списков) → {ID: {колонка: значение}}."""
    if not values or len(values) < 2:
        return {}
    header = values[0]
    out = {}
    for raw in values[1:]:
        r = {h: (raw[i] if i < len(raw) else "") for i, h in enumerate(header)}
        oid = str(r.get(COLUMNS[0], "")).strip()
        if oid:
            out[oid] = r
    return out


def _dev(r):
    name, inn = r.get("Застройщик (юрлицо)", ""), str(r.get("ИНН", "")).strip()
    return f"{name} (ИНН {inn})" if inn else name


def _flats(v):
    try:
        return int(float(str(v).replace(" ", "").replace(",", ".")))
    except ValueError:
        return None


def apply_history(curr, prev):
    """Заполняет «Первоначальный срок» и «Перенос»: из API, иначе из прошлой версии листа."""
    for oid, r in curr.items():
        q = r["Плановый ввод (квартал)"]
        if r["_initial_q"]:
            r["Первоначальный срок (если был перенос)"] = r["_initial_q"] if r["_initial_q"] != q else ""
            r["Перенос (да/нет)"] = "да" if r["_initial_q"] != q else "нет"
            continue
        p = prev.get(oid)
        if not p:
            continue  # истории нет — ячейки пустые, не придумываем
        first = p.get("Первоначальный срок (если был перенос)") or ""
        prev_q = p.get("Плановый ввод (квартал)") or ""
        if not first and prev_q and q and prev_q != q:
            first = prev_q
        r["Первоначальный срок (если был перенос)"] = first if first and first != q else ""
        if first and first != q:
            r["Перенос (да/нет)"] = "да"
        elif p.get("Перенос (да/нет)") or prev_q:
            r["Перенос (да/нет)"] = "нет"


def compute(curr, prev, today, gone_info=None):
    """Возвращает список строк для листа «Изменения» (порядок CHANGE_COLUMNS)."""
    d = today.isoformat()
    if not prev:
        flats = sum(_flats(r["Кол-во квартир"]) or 0 for r in curr.values())
        return [[d, T_INIT, "", "", "", "", "",
                 f"{len(curr)} объектов, {flats} квартир", ""]]

    gone_info = gone_info or {}
    out = []

    def add(t, r, was, now):
        out.append([d, t, r.get("ID объекта ЕИСЖС", ""), r.get("ЖК", ""),
                    r.get("Очередь / корпус", ""), r.get("Застройщик (юрлицо)", ""),
                    was, now, r.get("Ссылка на объект", "")])

    for oid, r in curr.items():
        p = prev.get(oid)
        if not p:
            add(T_NEW, r, "", f"{r['Плановый ввод (квартал)']}; квартир: {r['Кол-во квартир']}")
            continue
        if (p.get("Плановый ввод (квартал)") or "") != r["Плановый ввод (квартал)"]:
            add(T_MOVE, r, p.get("Плановый ввод (квартал)", ""), r["Плановый ввод (квартал)"])
        a, b = _flats(p.get("Кол-во квартир", "")), _flats(r["Кол-во квартир"])
        if a is not None and b is not None and a != b:
            add(T_FLATS, r, a, b)
        p_inn, c_inn = str(p.get("ИНН", "")).strip(), r["ИНН"]
        changed = (p_inn != c_inn) if (p_inn and c_inn) else \
            (p.get("Застройщик (юрлицо)", "") != r["Застройщик (юрлицо)"])
        if changed:
            add(T_DEV, r, _dev(p), _dev(r))

    for oid, p in prev.items():
        if oid not in curr:
            was = f"{p.get('Статус', '')}; срок {p.get('Плановый ввод (квартал)', '')}".strip("; ")
            add(T_GONE, p, was, gone_info.get(oid, "нет в выгрузке"))
    return out


def count_by_type(changes):
    res = {}
    for c in changes:
        res[c[1]] = res.get(c[1], 0) + 1
    return res
