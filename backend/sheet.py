"""
Чтение приватной Google-таблицы через Sheets API под service-аккаунтом.

Один вызов spreadsheets.get(includeGridData=True) даёт сразу:
  - значения ячеек (числа, строки, булевы),
  - тип формата (чтобы распознать ячейки-даты),
  - формулы (userEnteredValue.formulaValue) — нужны для границы «Осталось».

Возвращает прямоугольные rows и formulas (как getValues()/getFormulas() в Apps Script)
плюс таймзону таблицы.
"""

import os
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '1oTdRh6ZIDqU4IBoUZghRvGTdsIEBDtASYKD81PwGPrk')
SHEET_GID = int(os.environ.get('SHEET_GID', '2080478081'))

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
_SHEETS_EPOCH = datetime(1899, 12, 30)


def _credentials():
    """Service-account creds. Путь к JSON-ключу — в GOOGLE_APPLICATION_CREDENTIALS
    или SERVICE_ACCOUNT_FILE; иначе ADC (например, на Cloud Run).
    Относительный путь разрешается относительно папки backend (не текущей CWD)."""
    path = os.environ.get('SERVICE_ACCOUNT_FILE') or os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if path and not os.path.isabs(path):
        here = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.join(here, path)
        if os.path.exists(cand):
            path = cand
    if path and os.path.exists(path):
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    # ADC: на Cloud Run сработает привязанный к сервису аккаунт.
    import google.auth
    creds, _ = google.auth.default(scopes=SCOPES)
    return creds


def _service():
    return build('sheets', 'v4', credentials=_credentials(), cache_discovery=False)


def _serial_to_date(num):
    return (_SHEETS_EPOCH + timedelta(days=float(num))).date()


def _cell_value(cell):
    """(значение, формула) из ячейки grid data."""
    if not cell:
        return None, ''
    formula = ''
    uev = cell.get('userEnteredValue') or {}
    if 'formulaValue' in uev:
        formula = uev['formulaValue'] or ''
    ev = cell.get('effectiveValue')
    if ev is None:
        return None, formula
    if 'numberValue' in ev:
        num = ev['numberValue']
        fmt = (((cell.get('effectiveFormat') or {}).get('numberFormat') or {}).get('type'))
        if fmt in ('DATE', 'DATE_TIME'):
            return _serial_to_date(num), formula
        return num, formula
    if 'stringValue' in ev:
        return ev['stringValue'], formula
    if 'boolValue' in ev:
        return ev['boolValue'], formula
    return None, formula


def _sheet_title_and_tz(service):
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        fields='properties.timeZone,sheets.properties(sheetId,title)',
    ).execute()
    tz = (meta.get('properties') or {}).get('timeZone') or 'Europe/Moscow'
    title = None
    sheets = meta.get('sheets') or []
    for s in sheets:
        props = s.get('properties') or {}
        if props.get('sheetId') == SHEET_GID:
            title = props.get('title')
            break
    if title is None and sheets:
        title = (sheets[0].get('properties') or {}).get('title')
    return title, tz


def fetch_grid():
    """Возвращает (rows, formulas, tz). rows/formulas — прямоугольные (равная ширина)."""
    service = _service()
    title, tz = _sheet_title_and_tz(service)

    resp = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        ranges=[title] if title else None,
        includeGridData=True,
        fields='sheets.data.rowData.values('
               'effectiveValue,effectiveFormat.numberFormat.type,userEnteredValue.formulaValue)',
    ).execute()

    sheets = resp.get('sheets') or []
    if not sheets:
        return [], [], tz
    data = sheets[0].get('data') or []
    row_data = (data[0].get('rowData') if data else []) or []

    rows = []
    formulas = []
    width = 0
    for rd in row_data:
        cells = rd.get('values') or []
        vrow, frow = [], []
        for cell in cells:
            v, f = _cell_value(cell)
            vrow.append(v)
            frow.append(f)
        width = max(width, len(vrow))
        rows.append(vrow)
        formulas.append(frow)

    # Привести к прямоугольному виду (как getValues()/getFormulas()).
    for vrow, frow in zip(rows, formulas):
        if len(vrow) < width:
            vrow.extend([None] * (width - len(vrow)))
        if len(frow) < width:
            frow.extend([''] * (width - len(frow)))

    return rows, formulas, tz
