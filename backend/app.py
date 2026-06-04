"""
Бэкенд дашборда задолженности (Python / FastAPI).

  GET /            → отдаёт index.html (сам дашборд)
  GET /api/model   → JSON-модель (тот же формат, что прежде отдавал getModel в Apps Script)

Данные читаются из приватной таблицы через service-аккаунт (sheet.fetch_grid),
парсятся parser.build_model и кэшируются на CACHE_TTL секунд (опрос фронта — 60 сек,
кэш бережёт квоту Sheets API при нескольких зрителях).

Необязательная защита паролем (Basic Auth): задать DASH_USER и DASH_PASS в окружении.
Если они не заданы — доступ открыт (удобно для локального запуска).
"""

import os
import time
import secrets
import threading


def _load_dotenv():
    """Минимальный загрузчик .env (без зависимостей). Только для локального запуска."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


_load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import parser as model_parser
# sheet (Google API) импортируется лениво только в режиме MODEL_SOURCE=sheet —
# БД-деплою Google-библиотеки и креды не нужны.

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(ROOT, 'index.html')
ENTRY_HTML = os.path.join(ROOT, 'entry.html')
CACHE_TTL = int(os.environ.get('CACHE_TTL', '45'))

# Источник модели дашборда: 'sheet' (Google-таблица, по умолчанию) или 'db' (Postgres).
# Переключение без правки фронта — формат /api/model одинаковый.
MODEL_SOURCE = os.environ.get('MODEL_SOURCE', 'sheet').strip().lower()

app = FastAPI(title='Контроль задолженности · аренда авто')

# ---- Кэш модели (TTL) -----------------------------------------------------
_lock = threading.Lock()
_cache = {'at': 0.0, 'data': None}


def get_model():
    now = time.time()
    with _lock:
        if _cache['data'] is not None and (now - _cache['at']) < CACHE_TTL:
            return _cache['data']
    # Тяжёлое чтение — вне локана, чтобы не блокировать всех зрителей.
    try:
        if MODEL_SOURCE == 'db':
            import db_model
            from db import get_session
            session = get_session()
            try:
                data = db_model.build_model_from_db(session)
            finally:
                session.close()
        else:
            import sheet
            rows, formulas, tz = sheet.fetch_grid()
            data = model_parser.build_model(rows, formulas, tz)
    except Exception as err:  # noqa: BLE001
        import traceback
        return {'error': str(err), 'stack': traceback.format_exc()}
    with _lock:
        _cache['at'] = time.time()
        _cache['data'] = data
    return data


# ---- Необязательный Basic Auth -------------------------------------------
_security = HTTPBasic(auto_error=False)
_USER = os.environ.get('DASH_USER')
_PASS = os.environ.get('DASH_PASS')


def auth(creds: HTTPBasicCredentials = Depends(_security)):
    if not _USER and not _PASS:
        return  # защита выключена
    ok = (creds is not None
          and secrets.compare_digest(creds.username, _USER or '')
          and secrets.compare_digest(creds.password, _PASS or ''))
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Требуется вход',
            headers={'WWW-Authenticate': 'Basic'},
        )


# ---- Маршруты -------------------------------------------------------------
@app.get('/')
def index(_=Depends(auth)):
    return FileResponse(INDEX_HTML, media_type='text/html; charset=utf-8')


@app.get('/entry')
def entry(_=Depends(auth)):
    return FileResponse(ENTRY_HTML, media_type='text/html; charset=utf-8')


@app.get('/api/model')
def api_model(_=Depends(auth)):
    data = get_model()
    return JSONResponse(data)


@app.get('/healthz')
def healthz():
    return {'ok': True}


# ---- API оператора (этап 1) — под той же защитой, что и дашборд ----------
# Подключается, только если задан DATABASE_URL (иначе вводить деньги некуда).
if os.environ.get('DATABASE_URL'):
    import operator_api
    app.include_router(operator_api.router, dependencies=[Depends(auth)])


if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('PORT', '8080'))
    uvicorn.run('app:app', host='0.0.0.0', port=port, reload=False)
