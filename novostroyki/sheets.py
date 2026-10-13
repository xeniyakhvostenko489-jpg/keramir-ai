"""Запись в Google Таблицу через gspread и сервисный аккаунт."""
import json
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from . import config as C
from .diff import CHANGE_COLUMNS
from .parse import COLUMNS, letter

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

S_KORP, S_REF, S_CHG, S_SUM, S_Q, S_DEV, S_CHECK, S_METH = (
    "Корпуса", "Справочник ЖК", "Изменения", "Сводка по ЖК", "По кварталам",
    "По застройщикам", "Требует проверки", "Методика")
ORDER = [S_KORP, S_REF, S_CHG, S_SUM, S_Q, S_DEV, S_CHECK, S_METH]
REF_COLUMNS = ["ЖК", "Застройщик", "Класс", "Сайт ЖК", "Источник класса", "Проверено вручную (да/нет)"]

YELLOW = {"red": 1, "green": 0.95, "blue": 0.6}
GREEN = {"red": 0.8, "green": 0.94, "blue": 0.8}
GREY = {"red": 0.93, "green": 0.93, "blue": 0.93}


def connect():
    if not C.SPREADSHEET_ID:
        raise SystemExit("Не задан SPREADSHEET_ID в config/.env")
    if C.GOOGLE_SA_JSON:
        creds = Credentials.from_service_account_info(json.loads(C.GOOGLE_SA_JSON), scopes=SCOPES)
    else:
        path = Path(C.GOOGLE_KEY_PATH)
        if not path.is_absolute():
            path = C.ROOT / path
        if not path.exists():
            raise SystemExit(f"Не найден ключ сервисного аккаунта: {path}")
        creds = Credentials.from_service_account_file(str(path), scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(C.SPREADSHEET_ID)


def q(name):
    return f"'{name}'"


class Book:
    def __init__(self, sh):
        self.sh = sh
        self.ws = {}
        self._ensure_sheets()
        self.sep = self._detect_separator()

    # ---------- служебное ----------
    def _ensure_sheets(self):
        existing = {w.title: w for w in self.sh.worksheets()}
        for name in ORDER:
            self.ws[name] = existing.get(name) or self.sh.add_worksheet(name, rows=1000, cols=26)
        for title, w in existing.items():  # пустой «Лист1» от новой таблицы убираем
            if title not in ORDER and title in ("Лист1", "Sheet1") and not any(w.get_all_values()):
                self.sh.del_worksheet(w)
        self.sh.reorder_worksheets([self.ws[n] for n in ORDER])

    def _detect_separator(self):
        """Разделитель аргументов формул зависит от локали таблицы: ',' или ';'."""
        w = self.ws[S_METH]
        w.update(values=[["=SUM(1,2)"]], range_name="Z1", value_input_option="USER_ENTERED")
        ok = w.acell("Z1").value == "3"
        w.update(values=[[""]], range_name="Z1", value_input_option="RAW")
        return "," if ok else ";"

    def f(self, template):
        return template.replace("¦", self.sep)

    def _sheet_meta(self):
        return self.sh.fetch_sheet_metadata(
            {"fields": "sheets(properties(sheetId,title),conditionalFormats)"})["sheets"]

    def _base_format(self, ws, ncols, filter_=True):
        reqs = [
            {"updateSheetProperties": {"properties": {"sheetId": ws.id,
                                                      "gridProperties": {"frozenRowCount": 1}},
                                       "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1,
                                      "startColumnIndex": 0, "endColumnIndex": ncols},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                           "backgroundColor": GREY,
                                                           "wrapStrategy": "WRAP"}},
                            "fields": "userEnteredFormat(textFormat,backgroundColor,wrapStrategy)"}},
        ]
        if filter_:
            reqs.append({"setBasicFilter": {"filter": {"range": {
                "sheetId": ws.id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": ncols}}}})
        return reqs

    # ---------- чтение ----------
    def read_korpusa(self):
        return self.ws[S_KORP].get_all_values()

    def read_reference(self):
        values = self.ws[S_REF].get_all_values()
        if not values:
            self.ws[S_REF].update(values=[REF_COLUMNS], range_name="A1", value_input_option="RAW")
            return {}
        return {r[0].strip().lower(): r for r in values[1:] if r and r[0].strip()}

    # ---------- запись ----------
    def write_korpusa(self, rows, new_ids):
        ws = self.ws[S_KORP]
        n = len(rows)
        ws.clear()
        ws.resize(rows=max(n + 1, 2), cols=len(COLUMNS))
        values = [COLUMNS] + [[r[c] for c in COLUMNS] for r in rows]
        ws.update(values=values, range_name="A1", value_input_option="RAW")

        if n:
            g, s, w = letter("ЖК"), letter("Класс жилья (из справочника)"), letter("Комментарий")
            ref = q(S_REF)
            st = []
            v = []
            for i in range(2, n + 2):
                st.append([
                    self.f(f'=IFERROR(VLOOKUP(${g}{i}¦{ref}!$A:$F¦3¦0)¦"")'),
                    self.f(f'=IFERROR(VLOOKUP(${g}{i}¦{ref}!$A:$F¦4¦0)¦"")'),
                ])
                v.append([self.f(
                    f'=IF(OR({s}{i}=""¦{s}{i}="{C.CLASS_UNKNOWN}"¦'
                    f'IFERROR(VLOOKUP(${g}{i}¦{ref}!$A:$F¦6¦0)¦"")<>"да"¦LEFT({w}{i}¦1)="⚠")¦"да"¦"нет")')])
            t = letter("Сайт ЖК (из справочника)")
            vl = letter("Требует проверки (да/нет)")
            ws.update(values=st, range_name=f"{s}2:{t}{n + 1}", value_input_option="USER_ENTERED")
            ws.update(values=v, range_name=f"{vl}2:{vl}{n + 1}", value_input_option="USER_ENTERED")

        ncols = len(COLUMNS)
        reqs = [{"updateCells": {"range": {"sheetId": ws.id, "startRowIndex": 1},
                                 "fields": "userEnteredFormat"}}]
        meta = next(m for m in self._sheet_meta() if m["properties"]["sheetId"] == ws.id)
        reqs += [{"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}}
                 for _ in meta.get("conditionalFormats", [])]
        reqs += self._base_format(ws, ncols)
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": ncols}],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [
                {"userEnteredValue": f'=${letter("Перенос (да/нет)")}2="да"'}]},
                "format": {"backgroundColor": YELLOW}}}}})
        for i, r in enumerate(rows, start=1):
            if r["ID объекта ЕИСЖС"] in new_ids:
                reqs.append({"repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": i, "endRowIndex": i + 1,
                              "startColumnIndex": 0, "endColumnIndex": ncols},
                    "cell": {"userEnteredFormat": {"backgroundColor": GREEN}},
                    "fields": "userEnteredFormat.backgroundColor"}})
        self.sh.batch_update({"requests": reqs})

    def append_reference(self, new_rows):
        ws = self.ws[S_REF]
        if new_rows:
            ws.append_rows(new_rows, value_input_option="RAW", table_range="A1")
        reqs = self._base_format(ws, len(REF_COLUMNS))
        for col, values in ((2, C.CLASS_VALUES + [C.CLASS_UNKNOWN]), (5, ["да", "нет"])):
            reqs.append({"setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": col, "endColumnIndex": col + 1},
                "rule": {"condition": {"type": "ONE_OF_LIST",
                                       "values": [{"userEnteredValue": v} for v in values]},
                         "showCustomUi": True, "strict": False}}})
        self.sh.batch_update({"requests": reqs})

    def prepend_changes(self, changes):
        ws = self.ws[S_CHG]
        if not ws.get_all_values():
            ws.update(values=[CHANGE_COLUMNS], range_name="A1", value_input_option="RAW")
        if changes:
            ws.insert_rows(changes, row=2, value_input_option="RAW")
        self.sh.batch_update({"requests": self._base_format(ws, len(CHANGE_COLUMNS))})

    def write_summaries(self):
        k = q(S_KORP)
        L = {name: letter(name) for name in COLUMNS}
        ID, ZK, DEV, CLS, QTR, FL, SITE, CHK, CMT, LNK, BLD = (
            L["ID объекта ЕИСЖС"], L["ЖК"], L["Застройщик (юрлицо)"], L["Класс жилья (из справочника)"],
            L["Плановый ввод (квартал)"], L["Кол-во квартир"], L["Сайт застройщика"],
            L["Требует проверки (да/нет)"], L["Комментарий"], L["Ссылка на объект"], L["Очередь / корпус"])
        last = letter(COLUMNS[-1])

        sheets = {
            S_SUM: [self.f(
                f'=QUERY({k}!A1:{last}¦"select {ZK}, {DEV}, {CLS}, count({ID}), sum({FL}), '
                f"min({QTR}), max({QTR}) where {ZK} <> '' group by {ZK}, {DEV}, {CLS} order by {ZK} "
                f"label {DEV} 'Застройщик', {CLS} 'Класс', count({ID}) 'Число корпусов', "
                f"sum({FL}) 'Сумма квартир', min({QTR}) 'Ближайший срок ввода', "
                f"max({QTR}) 'Последний срок ввода'\"¦1)")],
        }
        # По кварталам
        qrows = [["Квартал", "Корпусов", "Квартир", "ЖК"], [
            self.f(f'=IFERROR(SORT(UNIQUE(FILTER({k}!{QTR}2:{QTR}¦{k}!{QTR}2:{QTR}<>"")))¦"")'),
            self.f(f'=MAP(A2:A¦LAMBDA(x¦IF(x=""¦""¦COUNTIF({k}!{QTR}2:{QTR}¦x))))'),
            self.f(f'=MAP(A2:A¦LAMBDA(x¦IF(x=""¦""¦SUMIFS({k}!{FL}2:{FL}¦{k}!{QTR}2:{QTR}¦x))))'),
            self.f(f'=MAP(A2:A¦LAMBDA(x¦IF(x=""¦""¦COUNTUNIQUEIFS({k}!{ZK}2:{ZK}¦{k}!{QTR}2:{QTR}¦x))))'),
        ]]
        drows = [["Застройщик", "Число ЖК", "Корпусов", "Квартир", "Сайт"], [
            self.f(f'=IFERROR(SORT(UNIQUE(FILTER({k}!{DEV}2:{DEV}¦{k}!{DEV}2:{DEV}<>"")))¦"")'),
            self.f(f'=MAP(A2:A¦LAMBDA(x¦IF(x=""¦""¦COUNTUNIQUEIFS({k}!{ZK}2:{ZK}¦{k}!{DEV}2:{DEV}¦x))))'),
            self.f(f'=MAP(A2:A¦LAMBDA(x¦IF(x=""¦""¦COUNTIF({k}!{DEV}2:{DEV}¦x))))'),
            self.f(f'=MAP(A2:A¦LAMBDA(x¦IF(x=""¦""¦SUMIFS({k}!{FL}2:{FL}¦{k}!{DEV}2:{DEV}¦x))))'),
            self.f(f'=MAP(A2:A¦LAMBDA(x¦IF(x=""¦""¦IFERROR(INDEX(FILTER({k}!{SITE}2:{SITE}¦'
                   f'{k}!{DEV}2:{DEV}=x¦{k}!{SITE}2:{SITE}<>"")¦1)¦""))))'),
        ]]
        ref = q(S_REF)
        crow = [[
            self.f(f'=IFERROR(QUERY({ref}!A1:F¦"select A, B, C, E, F where A <> \'\' and '
                   f"(C = '' or C is null or C = '{C.CLASS_UNKNOWN}' or F <> 'да' or F is null) "
                   f"label A 'ЖК (справочник)'\"¦1)¦\"нет\")"),
            "", "", "", "", "",
            self.f(f'=IFERROR(QUERY({k}!A1:{last}¦"select {ID}, {ZK}, {BLD}, {DEV}, {CLS}, {CMT}, {LNK} '
                   f"where {CHK} = 'да'\"¦1)¦\"нет\")"),
        ]]

        for name, formulas in sheets.items():
            w = self.ws[name]
            w.clear()
            w.update(values=[formulas], range_name="A1", value_input_option="USER_ENTERED")
        for name, rows in ((S_Q, qrows), (S_DEV, drows), (S_CHECK, crow)):
            w = self.ws[name]
            w.clear()
            w.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")

        reqs = []
        for name, ncols in ((S_SUM, 7), (S_Q, 4), (S_DEV, 5), (S_CHECK, 12)):
            reqs += self._base_format(self.ws[name], ncols, filter_=name != S_CHECK)
        self.sh.batch_update({"requests": reqs})

    def write_method(self, lines):
        w = self.ws[S_METH]
        w.clear()
        w.update(values=lines, range_name="A1", value_input_option="RAW")
        self.sh.batch_update({"requests": self._base_format(w, 2, filter_=False)})
