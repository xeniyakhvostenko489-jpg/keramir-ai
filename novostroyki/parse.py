"""Нормализация объектов ЕИСЖС в строки листа «Корпуса».

Имена полей API перечислены кандидатами: берётся первое непустое.
Если наш.дом.рф переименует поле — поправьте FIELDS (python update.py --inspect
покажет реальные ключи).
"""
import re
from datetime import date, datetime, timezone

from . import config as C

COLUMNS = [
    "ID объекта ЕИСЖС", "Ссылка на объект", "Застройщик (юрлицо)", "ИНН",
    "Группа компаний", "Сайт застройщика", "ЖК", "Очередь / корпус", "Адрес", "Район",
    "Плановый ввод (квартал)", "Плановый ввод (год)",
    "Первоначальный срок (если был перенос)", "Перенос (да/нет)", "Статус",
    "Кол-во квартир", "Жилая площадь, м²", "Этажность",
    "Класс жилья (из справочника)", "Сайт ЖК (из справочника)",
    "Дата обновления", "Требует проверки (да/нет)", "Комментарий",
]
COL = {name: i for i, name in enumerate(COLUMNS)}


def letter(name):
    return chr(ord("A") + COL[name])


FIELDS = {
    "id": ["objId", "id"],
    "address": ["objAddr", "address", "objAddress"],
    "complex": ["objCommercNm", "complexName", "objComplexNm", "rpdNm"],
    "building": ["objBuildNm", "buildingName", "objName", "objNm"],
    "dev_name": ["developer.devShortCleanNm", "developer.devShortNm",
                 "developer.devFullCleanNm", "devShortCleanNm", "devShortNm"],
    "dev_inn": ["developer.devInn", "devInn"],
    "dev_group": ["developer.developerGroupName", "developer.devGroupName",
                  "developer.groupName", "developerGroupName", "devGroupName"],
    "dev_site": ["developer.devSite", "devSite"],
    "complex_site": ["objSiteUrl", "complexSite", "objComplexSite", "siteUrl"],
    "district": ["objDistrictNm", "districtName", "objDistrict", "objAreaNm"],
    "planned": ["objReady100PercDt", "objPlanReadyDt", "readyDt", "objReadyDt"],
    "initial": ["objReady100PercDtInitial", "objFirstReadyDt", "objInitReadyDt",
                "objReadyDtInitial"],
    "status": ["siteStatus", "objStatusNm", "objStatusName"],
    "status_code": ["objStatus"],
    "flats": ["objElemLivingCnt", "objFlatCnt", "flatCnt", "objLivingElemCnt"],
    "living_area": ["objSquareLiving", "objLivingSquare", "objLivSquare"],
    "floor_min": ["objFloorMin", "floorMin"],
    "floor_max": ["objFloorMax", "floorMax", "objFloorCnt"],
}

# Поля, ради которых догружаем карточку объекта, если их нет в списке
CARD_FIELDS = ["dev_inn", "dev_group", "dev_site", "complex", "flats",
               "living_area", "floor_max", "planned", "district"]


def _dig(obj, path):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def pick(obj, key):
    for path in FIELDS[key]:
        v = _dig(obj, path)
        if v not in (None, "", [], {}):
            return v
    return None


def missing_fields(obj):
    return [k for k in CARD_FIELDS if pick(obj, k) is None]


def merge(base, extra):
    """Дополняет base полями из extra, не затирая имеющиеся."""
    if not isinstance(extra, dict):
        return base
    out = dict(base)
    for k, v in extra.items():
        if out.get(k) in (None, "", [], {}):
            out[k] = v
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = merge(out[k], v)
    return out


def to_date(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):  # epoch, мс
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc).date()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    m = re.search(r"([1-4])\s*(?:кв|квартал)\S*\s*(\d{4})", s, re.I)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        return date(y, q * 3, 28)
    m = re.match(r"(\d{4})-(\d{2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    return None


def quarter_label(d):
    """'2026, 4 кв.' — сортируется как текст в хронологическом порядке."""
    return f"{d.year}, {(d.month - 1) // 3 + 1} кв." if d else ""


BUILDING_RE = re.compile(
    r"((?:очередь|корпус|корп\.|секция|блок|литер|строение|стр\.|ГП|дом)\s*[№#]?\s*[\w\-./]+)", re.I)


def building_label(obj):
    b = pick(obj, "building")
    if b:
        return str(b).strip()
    addr = pick(obj, "address") or ""
    found = BUILDING_RE.findall(addr)
    return ", ".join(found) if found else ""


def is_ekb(obj):
    return C.CITY.lower() in str(pick(obj, "address") or "").lower()


def is_building(obj):
    code = pick(obj, "status_code")
    if code is not None and str(code) != "0":
        return False
    st = str(pick(obj, "status") or "").lower()
    return not st or "стро" in st


def clean_site(v):
    if not v:
        return ""
    v = str(v).strip()
    if v and not v.startswith("http"):
        v = "http://" + v
    return v


def num(v):
    if v in (None, ""):
        return ""
    try:
        f = float(str(v).replace(",", ".").replace(" ", ""))
        return int(f) if f.is_integer() else round(f, 2)
    except ValueError:
        return ""


def to_row(obj, today):
    """Словарь {колонка: значение}. Класс/сайт ЖК/«требует проверки» — формулы, ставит sheets.py."""
    oid = str(pick(obj, "id"))
    planned = to_date(pick(obj, "planned"))
    initial = to_date(pick(obj, "initial"))
    fmin, fmax = num(pick(obj, "floor_min")), num(pick(obj, "floor_max"))
    floors = f"{fmin}–{fmax}" if fmin and fmax and fmin != fmax else (fmax or fmin or "")

    notes = []
    if not planned:
        notes.append("нет планового срока в ЕИСЖС")
    elif planned < today:
        notes.append("плановый срок уже прошёл")
    if not pick(obj, "complex"):
        notes.append("нет названия ЖК в ЕИСЖС")
    if num(pick(obj, "flats")) == "":
        notes.append("нет кол-ва квартир")

    row = {c: "" for c in COLUMNS}
    row.update({
        "ID объекта ЕИСЖС": oid,
        "Ссылка на объект": C.OBJECT_URL.format(id=oid),
        "Застройщик (юрлицо)": str(pick(obj, "dev_name") or "").strip(),
        "ИНН": str(pick(obj, "dev_inn") or "").strip(),
        "Группа компаний": str(pick(obj, "dev_group") or "").strip(),
        "Сайт застройщика": clean_site(pick(obj, "dev_site")),
        "ЖК": str(pick(obj, "complex") or "").strip(),
        "Очередь / корпус": building_label(obj),
        "Адрес": str(pick(obj, "address") or "").strip(),
        "Район": str(pick(obj, "district") or "").strip(),
        "Плановый ввод (квартал)": quarter_label(planned),
        "Плановый ввод (год)": planned.year if planned else "",
        "Статус": str(pick(obj, "status") or "").strip(),
        "Кол-во квартир": num(pick(obj, "flats")),
        "Жилая площадь, м²": num(pick(obj, "living_area")),
        "Этажность": floors,
        "Дата обновления": today.isoformat(),
        "Комментарий": ("⚠ " + "; ".join(notes)) if notes else "",
    })
    row["_planned"] = planned
    row["_initial_q"] = quarter_label(initial)
    row["_complex_site"] = clean_site(pick(obj, "complex_site"))
    return row


def in_range(row):
    p = row["_planned"]
    return p is None or p <= C.DEADLINE
