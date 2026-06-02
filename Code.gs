/**
 * Лизинговый дашборд — backend (Google Apps Script Web App).
 *
 * Парсит приватную Google-таблицу с вертикально сложенными блоками-месяцами.
 * doGet отдаёт сам дашборд (HTML), фронт получает данные через google.script.run
 * → getModel(). Тот же домен, авторизация по аккаунту mozen.ru, без CORS, данные
 * НЕ публикуются. Фронт опрашивает getModel() раз в 60 сек.
 *
 * Деплой:
 *   1. Расширения → Apps Script.
 *   2. Создать два файла:
 *        Code.gs   — этот скрипт;
 *        Index     — HTML-файл (File → New → HTML), вставить содержимое index.html.
 *   3. Выполнить getModel один раз вручную → выдать разрешения на доступ к таблице.
 *   4. Развернуть → Новое развёртывание → Веб-приложение:
 *        Запуск от имени:  Я
 *        Доступ:           Только пользователи mozen.ru  (данные остаются приватными)
 *   5. Открыть URL .../exec — это и есть готовый дашборд.
 */

var SPREADSHEET_ID = '1oTdRh6ZIDqU4IBoUZghRvGTdsIEBDtASYKD81PwGPrk';
var SHEET_GID = 2080478081; // лист из ссылки; если не найден — берём первый

// ---------------------------------------------------------------------------
// Точки входа
// ---------------------------------------------------------------------------

/** Отдаёт сам дашборд (HTML-файл "Index" в проекте). */
function doGet(e) {
  return HtmlService
    .createHtmlOutputFromFile('Index')
    .setTitle('Контроль задолженности · аренда авто')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** Вызывается фронтом через google.script.run — возвращает готовую модель. */
function getModel() {
  try {
    return buildModel();
  } catch (err) {
    return { error: String(err && err.message || err), stack: err && err.stack };
  }
}

/**
 * ДИАГНОСТИКА. Запустить вручную (Выполнить) и прислать вывод из "Логи".
 * Показывает помесячно: сколько водителей найдено и какую сумму "Осталось"
 * насчитал парсер — чтобы сравнить с реальными итогами в таблице.
 */
function debugMonths() {
  var model = buildModel();
  if (model.error) { Logger.log('ОШИБКА: ' + model.error + '\n' + model.stack); return; }

  var byMonth = {}; // label -> {sum, n, isCurrent, names:[]}
  model.drivers.forEach(function (d) {
    d.monthly.forEach(function (e) {
      var b = byMonth[e.label] || (byMonth[e.label] = { sum: 0, n: 0, isCurrent: e.isCurrent, names: [] });
      b.sum += e.remaining; b.n++; b.names.push(d.name + '=' + Math.round(e.remaining));
    });
  });

  Logger.log('today=' + model.today + '  tz=' + model.timezone +
             '  месяцев=' + model.months.length + '  водителей=' + model.drivers.length);
  Logger.log('текущий месяц: ' + model.currentMonthLabel);
  var closedNet = 0;
  model.months.forEach(function (m) {
    var b = byMonth[m.label] || { sum: 0, n: 0 };
    Logger.log('[' + (m.isCurrent ? 'ТЕКУЩ' : 'закрыт') + '] ' + m.label +
      ' (' + m.year + '-' + pad2(m.month + 1) + ') | водителей=' + b.n +
      ' | Осталось(сумма)=' + Math.round(b.sum));
    if (!m.isCurrent) closedNet += b.sum;
  });
  Logger.log('--- ИТОГО закрытые net = ' + Math.round(closedNet));
  Logger.log('--- model.totals.netBalance = ' + model.totals.netBalance);
  Logger.log('--- model.totals.totalDebt (валовый, только должники) = ' + model.totals.totalDebt);

  // Топ-5 вкладов в расхождение: самые крупные отрицательные балансы по (водитель×месяц).
  var flat = [];
  model.drivers.forEach(function (d) {
    d.monthly.forEach(function (e) { if (!e.isCurrent) flat.push({ s: e.remaining, t: d.name + ' / ' + e.label + ' / авто ' + (e.car || '—') }); });
  });
  flat.sort(function (a, b) { return a.s - b.s; });
  Logger.log('--- крупнейшие отрицательные (закрытые) ---');
  flat.slice(0, 8).forEach(function (x) { Logger.log('  ' + Math.round(x.s) + '  ' + x.t); });
}

/**
 * ДИАГНОСТИКА структуры одного месяца. Запустить, напр., dumpMart / dumpFev / dumpApr
 * (см. обёртки ниже) и прислать "Логи". Показывает классификацию колонок и значения
 * строк "сумма мес." / "Номер авто" / "Итого" / "Осталось" по колонкам.
 */
function dumpMonth(monthName) {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var rows = pickSheet(ss).getDataRange().getValues();
  var target = norm(monthName);
  var starts = [];
  for (var i = 0; i < rows.length; i++) if (rowType(rows[i]) === 'sutki') starts.push(i);

  for (var b = 0; b < starts.length; b++) {
    var start = starts[b], end = (b + 1 < starts.length) ? starts[b + 1] : rows.length;
    var namesRow = rows[start + 1] || [];
    var matched = false;
    for (var c = 0; c < namesRow.length; c++) {
      if (norm(namesRow[c]).indexOf(target) >= 0) { matched = true; break; }
    }
    if (!matched) continue;

    Logger.log('=== Блок ' + monthName + ' (строки ' + start + '..' + (end - 1) + ') ===');
    var cls = [];
    for (var cc = 0; cc < namesRow.length; cc++) {
      var raw = namesRow[cc];
      if (raw === '' || raw === null || raw === undefined) continue;
      var nm = norm(raw), kind;
      if (isMonthName(nm)) kind = 'МЕСЯЦ';
      else if (isVykup(nm)) kind = 'ВЫКУП';
      else if (isSutki(nm)) kind = 'проп';
      else kind = 'ВОДИТЕЛЬ';
      cls.push('c' + cc + '=' + kind + ':"' + cleanName(raw) + '"');
    }
    Logger.log('КОЛОНКИ имён: ' + cls.join(' | '));

    ['summaMes', 'avto', 'itogo', 'ostalos'].forEach(function (want) {
      for (var r = start + 1; r < end; r++) {
        if (rowType(rows[r]) === want) {
          var out = [];
          for (var c2 = 0; c2 < rows[r].length; c2++) {
            var v = rows[r][c2];
            if (v === '' || v === null || v === undefined) continue;
            out.push('c' + c2 + '=' + v);
          }
          Logger.log(want + ': ' + out.join(' | '));
          return;
        }
      }
    });
    return;
  }
  Logger.log('Блок не найден: ' + monthName);
}
function dumpFev() { dumpMonth('Февраль'); }
function dumpMart() { dumpMonth('Март'); }
function dumpApr() { dumpMonth('Апрель'); }

/**
 * ДИАГНОСТИКА итоговых ячеек. Запустить и прислать "Логи".
 * Показывает по каждому месяцу значение и ФОРМУЛУ ячейки "Осталось"-итог (c56) —
 * чтобы понять, какие именно колонки лист включает в официальный месячный итог.
 */
function dumpTotalFormulas() {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = pickSheet(ss);
  var rows = sheet.getDataRange().getValues();
  var formulas = sheet.getDataRange().getFormulas();

  var starts = [];
  for (var i = 0; i < rows.length; i++) if (rowType(rows[i]) === 'sutki') starts.push(i);

  for (var b = 0; b < starts.length; b++) {
    var start = starts[b], end = (b + 1 < starts.length) ? starts[b + 1] : rows.length;
    var namesRow = rows[start + 1] || [];
    var label = '';
    for (var c = 0; c < namesRow.length; c++) {
      if (isMonthName(norm(namesRow[c]))) { label = cleanName(namesRow[c]); break; }
    }
    var summaMesRow = null, ostIdx = -1, itogoIdx = -1;
    for (var r = start + 1; r < end; r++) {
      var t = rowType(rows[r]);
      if (t === 'summaMes' && !summaMesRow) summaMesRow = rows[r];
      if (t === 'ostalos' && ostIdx < 0) ostIdx = r;
      if (t === 'itogo' && itogoIdx < 0) itogoIdx = r;
    }
    var tc = findTotalCol(summaMesRow);
    var tcLabel = (tc === Infinity) ? 'нет' : ('c' + tc);
    var ostVal = (ostIdx >= 0 && tc !== Infinity) ? rows[ostIdx][tc] : '?';
    var ostFla = (ostIdx >= 0 && tc !== Infinity) ? formulas[ostIdx][tc] : '?';
    var sumFla = (summaMesRow && tc !== Infinity) ? formulas[rows.indexOf(summaMesRow)][tc] : '?';
    var itoFla = (itogoIdx >= 0 && tc !== Infinity) ? formulas[itogoIdx][tc] : '?';
    Logger.log(label + ' | итог=' + tcLabel +
      ' | Осталось.итог=' + ostVal +
      ' | формула_Осталось="' + ostFla + '"' +
      ' | формула_суммМес="' + sumFla + '"' +
      ' | формула_Итого="' + itoFla + '"');
  }
}

// ---------------------------------------------------------------------------
// Утилиты парсинга
// ---------------------------------------------------------------------------

/** Нормализация ярлыка строки: нижний регистр, схлопнутые пробелы. */
function norm(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(/ /g, ' ').trim().toLowerCase().replace(/\s+/g, ' ');
}

/** Отображаемое имя водителя: trim + схлопнуть двойные пробелы (регистр сохраняем). */
function cleanName(v) {
  return String(v).replace(/ /g, ' ').trim().replace(/\s+/g, ' ');
}

/** Ключ для сопоставления ФИО между месяцами (единый регистр). */
function nameKey(v) {
  return cleanName(v).toLowerCase();
}

/**
 * Парсинг числа в обоих форматах:
 *   "-49 600,00" (пробел/неразрывный пробел = тысячи, запятая = десятичная)
 *   "-99000"     (обычное число)
 * Возвращает float или 0 для пустого/нечислового.
 */
function parseNum(v) {
  if (v === null || v === undefined || v === '') return 0;
  if (typeof v === 'number') return v;
  var s = String(v).replace(/ /g, ' ').trim();
  if (s === '') return 0;
  s = s.replace(/\s/g, '');   // убрать разделители тысяч
  s = s.replace(/,/g, '.');   // десятичная запятая → точка
  s = s.replace(/[^0-9.\-]/g, ''); // выбросить ₽ и прочий мусор
  var n = parseFloat(s);
  return isNaN(n) ? 0 : n;
}

var MONTH_STEMS = [
  ['янв', 0], ['фев', 1], ['март', 2], ['мар', 2], ['апр', 3],
  ['май', 4], ['мая', 4], ['июн', 5], ['июл', 6], ['авг', 7],
  ['сен', 8], ['окт', 9], ['ноя', 10], ['дек', 11]
];

function monthFromLabel(s) {
  var n = norm(s);
  for (var i = 0; i < MONTH_STEMS.length; i++) {
    if (n.indexOf(MONTH_STEMS[i][0]) >= 0) return MONTH_STEMS[i][1];
  }
  return -1;
}

function isMonthName(s) { return monthFromLabel(s) >= 0; }

function isSutki(s)     { return s.indexOf('сутки') >= 0; }
function isSummaMes(s)  { return s.indexOf('сумма мес') >= 0; }
function isNomerAvto(s) { return s.indexOf('номер авто') >= 0 || s.indexOf('номер машины') >= 0; }
function isItogo(s)     { return s.indexOf('итого') >= 0; }
function isOstalos(s)   { return s.indexOf('осталось') >= 0 || s.indexOf('остаток') >= 0; }
function isVykup(s)     { return s.indexOf('выкуп') >= 0; }

/**
 * Индекс колонки-ИТОГА месяца. В строке "сумма мес." это агрегат (сумма всех
 * водителей), по модулю он кратно больше любого отдельного обязательства.
 * Всё на этой колонке и ПРАВЕЕ — отдельная секция (доп. машины/выкуп), в месячный
 * итог листа не входит. Возвращает Infinity, если явного итога нет.
 */
function findTotalCol(summaMesRow) {
  if (!summaMesRow) return Infinity;
  var best = -1, bestVal = 0, second = 0;
  for (var c = 0; c < summaMesRow.length; c++) {
    var v = Math.abs(parseNum(summaMesRow[c]));
    if (v > bestVal) { second = bestVal; bestVal = v; best = c; }
    else if (v > second) { second = v; }
  }
  return (best >= 0 && second > 0 && bestVal >= 2 * second) ? best : Infinity;
}

/** Буквы столбца A1 → 0-индексный номер. "A"→0, "BA"→52, "BD"→55. */
function colLettersToIndex(letters) {
  var idx = 0;
  for (var i = 0; i < letters.length; i++) {
    idx = idx * 26 + (letters.charCodeAt(i) - 64); // 'A'=65
  }
  return idx - 1;
}

/**
 * Разбирает формулу итоговой ячейки вида "=SUM(B39:BA39)" и возвращает
 * {start, end} — 0-индексные границы столбцов диапазона. null, если не распознано.
 */
function formulaColRange(formula) {
  if (!formula) return null;
  var m = String(formula).match(/([A-Z]+)\d+\s*:\s*([A-Z]+)\d+/);
  if (!m) return null;
  var a = colLettersToIndex(m[1]);
  var b = colLettersToIndex(m[2]);
  return { start: Math.min(a, b), end: Math.max(a, b) };
}

/** Классификация строки по любому её непустому ярлыку. */
function rowType(row) {
  for (var c = 0; c < row.length; c++) {
    var s = norm(row[c]);
    if (!s) continue;
    if (isSutki(s))     return 'sutki';
    if (isSummaMes(s))  return 'summaMes';
    if (isNomerAvto(s)) return 'avto';
    if (isOstalos(s))   return 'ostalos';
    if (isItogo(s))     return 'itogo';
  }
  return 'data';
}

// ---------------------------------------------------------------------------
// Построение модели
// ---------------------------------------------------------------------------
function buildModel() {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = pickSheet(ss);
  var range = sheet.getDataRange();
  var rows = range.getValues();
  var formulas = range.getFormulas();

  var tz = ss.getSpreadsheetTimeZone() || Session.getScriptTimeZone() || 'Europe/Moscow';
  var nowStr = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  var tParts = nowStr.split('-');
  var today = { y: +tParts[0], m: (+tParts[1]) - 1, d: +tParts[2] };
  var todayMs = Date.UTC(today.y, today.m, today.d);

  // 1) Найти индексы стартов блоков (строки "Сумма/сутки").
  var blockStarts = [];
  for (var i = 0; i < rows.length; i++) {
    if (rowType(rows[i]) === 'sutki') blockStarts.push(i);
  }

  var months = [];      // метаданные месяцев
  var records = [];     // плоский список {key,name,car,monthIndex,obligation,paid,remaining,lastPaymentDay}
  var buyoutByMonth = [];

  for (var b = 0; b < blockStarts.length; b++) {
    var start = blockStarts[b];
    var end = (b + 1 < blockStarts.length) ? blockStarts[b + 1] : rows.length;

    var namesRow = rows[start + 1] || [];

    // Колонки водителей: непустые ячейки строки имён, кроме месяца и ВЫКУП.
    var driverCols = {}; // col -> {name,key}
    var vykupCols = [];
    var monthLabel = '';
    for (var c = 0; c < namesRow.length; c++) {
      var raw = namesRow[c];
      if (raw === null || raw === undefined || String(raw).trim() === '') continue;
      var nm = norm(raw);
      if (isMonthName(nm)) { if (!monthLabel) monthLabel = cleanName(raw); continue; }
      if (isVykup(nm)) { vykupCols.push(c); continue; }
      if (isSutki(nm)) continue;
      driverCols[c] = { name: cleanName(raw), key: nameKey(raw) };
    }

    // Якорные строки внутри блока.
    var summaMesRow = null, avtoRow = null, itogoRow = null, ostalosRow = null;
    var dataRows = [];
    for (var r = start + 1; r < end; r++) {
      var t = rowType(rows[r]);
      if (t === 'summaMes' && !summaMesRow) summaMesRow = rows[r];
      else if (t === 'avto' && !avtoRow) avtoRow = rows[r];
      else if (t === 'itogo' && !itogoRow) itogoRow = rows[r];
      else if (t === 'ostalos' && !ostalosRow) ostalosRow = rows[r];
    }
    // Ежедневные строки = data-строки между "Номер авто" и "Итого".
    var dayStart = avtoRow ? rows.indexOf(avtoRow) + 1 : start + 2;
    var dayEnd = itogoRow ? rows.indexOf(itogoRow) : end;
    for (var d = dayStart; d < dayEnd; d++) {
      if (d < 0 || d >= rows.length) continue;
      if (rowType(rows[d]) === 'data') dataRows.push(rows[d]);
    }

    // Граница колонок водителей = диапазон официальной формулы «Осталось» (итоговая ячейка).
    // Так дашборд совпадает с месячным итогом листа точно (лист может НЕ включать
    // крайние правые столбцы — поздно добавленных водителей вне месячного итога).
    var totalCol = findTotalCol(summaMesRow);
    var colRange = null;
    if (ostalosRow && totalCol !== Infinity) {
      var ostRowIdx = rows.indexOf(ostalosRow);
      if (ostRowIdx >= 0 && formulas[ostRowIdx]) {
        colRange = formulaColRange(formulas[ostRowIdx][totalCol]);
      }
    }
    if (colRange) {
      // Оставляем только колонки внутри диапазона SUM (включительно), исключая саму итоговую.
      for (var dck in driverCols) {
        var ci = +dck;
        if (ci < colRange.start || ci > colRange.end || ci >= totalCol) delete driverCols[dck];
      }
    } else {
      // Запасной путь: отсекаем колонку-итог и всё правее неё.
      for (var dck2 in driverCols) { if (+dck2 >= totalCol) delete driverCols[dck2]; }
    }

    // Месяц/год блока — из ячеек-дат ежедневных строк (надёжнее ярлыка).
    var by = -1, bm = -1;
    for (var dr = 0; dr < dataRows.length; dr++) {
      var cell0 = dataRows[dr][0];
      if (cell0 instanceof Date) { by = cell0.getFullYear(); bm = cell0.getMonth(); break; }
    }
    if (bm < 0) { // запасной путь — из ярлыка месяца
      bm = monthFromLabel(monthLabel);
      var ym = String(monthLabel).match(/(20\d{2})/);
      by = ym ? +ym[1] : today.y;
      if (bm < 0) bm = today.m;
    }
    var daysInMonth = new Date(by, bm + 1, 0).getDate();
    var isCurrent = (by === today.y && bm === today.m);
    var daysElapsed = isCurrent ? Math.min(today.d, daysInMonth) : daysInMonth;

    var monthIndex = months.length;
    months.push({
      index: monthIndex,
      label: monthLabel || (bm + 1) + '.' + by,
      year: by, month: bm,
      isCurrent: isCurrent,
      daysInMonth: daysInMonth,
      daysElapsed: daysElapsed,
      sortKey: by * 12 + bm
    });

    // ВЫКУП — отдельный поток, суммируем по месяцу.
    var vykupTotal = 0;
    for (var vc = 0; vc < vykupCols.length; vc++) {
      for (var vr = 0; vr < dataRows.length; vr++) {
        vykupTotal += Math.max(0, parseNum(dataRows[vr][vykupCols[vc]]));
      }
    }
    if (vykupTotal) buyoutByMonth.push({ monthIndex: monthIndex, total: vykupTotal });

    // Запись по каждому водителю (колонки левее итоговой).
    var hasOstalos = !!ostalosRow;
    for (var col in driverCols) {
      col = +col;
      var obligation = summaMesRow ? parseNum(summaMesRow[col]) : 0;

      var paid = 0, lastDay = 0, payDays = 0;
      for (var pr = 0; pr < dataRows.length; pr++) {
        var val = parseNum(dataRows[pr][col]);
        if (val > 0) {
          paid += val;
          payDays++;
          var dc = dataRows[pr][0];
          var dayNum = (dc instanceof Date) ? dc.getDate() : (pr + 1);
          if (dayNum > lastDay) lastDay = dayNum;
        }
      }
      var itogoPaid = itogoRow ? parseNum(itogoRow[col]) : 0;
      if (itogoPaid > 0) paid = itogoPaid; // "Итого" авторитетнее, если есть

      // Баланс — строго из строки "Осталось" (пустая ячейка = 0): это итог листа,
      // поэтому сумма по колонкам совпадёт с месячным итогом. Запасной путь
      // (обязательство + оплата) — только если строки "Осталось" в блоке нет.
      var remaining = hasOstalos ? parseNum(ostalosRow[col]) : (obligation + paid);

      // Активен в месяце = есть обязательство. Но колонку с переплатой/переносом
      // (обязательство 0, но "Осталось" ≠ 0) тоже учитываем в балансе — лист её
      // включает в месячный итог. Пропускаем только полностью пустую колонку.
      var active = obligation !== 0;
      if (!obligation && !paid && !remaining) continue;

      var car = avtoRow ? cleanName(avtoRow[col]) : '';

      records.push({
        key: driverCols[col].key,
        name: driverCols[col].name,
        car: car,
        monthIndex: monthIndex,
        obligation: obligation,
        paid: paid,
        remaining: remaining,
        active: active,
        lastPaymentDay: lastDay,
        paymentDays: payDays
      });
    }
  }

  // "Текущий" (незакрытый) месяц = самый поздний блок ПО ДАТЕ, а не календарное
  // "сегодня". Так последний идущий месяц (напр. июнь) не попадает в накопленный
  // долг, даже если реальная дата системы расходится с данными таблицы.
  var latest = null;
  for (var mi = 0; mi < months.length; mi++) {
    if (!latest || months[mi].sortKey > latest.sortKey) latest = months[mi];
  }
  for (var mj = 0; mj < months.length; mj++) {
    var mm = months[mj];
    mm.isCurrent = !!latest && mm.index === latest.index;
    if (mm.isCurrent) {
      var within = (mm.year === today.y && mm.month === today.m);
      mm.daysElapsed = within ? Math.min(today.d, mm.daysInMonth) : mm.daysInMonth;
    } else {
      mm.daysElapsed = mm.daysInMonth;
    }
  }

  return aggregate(records, months, buyoutByMonth, today, todayMs, tz);
}

function pickSheet(ss) {
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    if (sheets[i].getSheetId() === SHEET_GID) return sheets[i];
  }
  return sheets[0];
}

// ---------------------------------------------------------------------------
// Агрегация в финальную модель
// ---------------------------------------------------------------------------
function aggregate(records, months, buyoutByMonth, today, todayMs, tz) {
  var byKey = {};
  for (var i = 0; i < records.length; i++) {
    var rec = records[i];
    if (!byKey[rec.key]) byKey[rec.key] = { key: rec.key, name: rec.name, recs: [] };
    byKey[rec.key].recs.push(rec);
    // самое свежее имя/авто
    byKey[rec.key].name = rec.name;
  }

  var monthsBySort = months.slice().sort(function (a, b) { return a.sortKey - b.sortKey; });

  var drivers = [];
  var aging = { current: 0, m1: 0, m23: 0, m3plus: 0 };
  var netBalance = 0, grossDebt = 0, debtorsCount = 0, debtOver2m = 0;
  var curObSum = 0, curPaidSum = 0;

  var currentMonth = null;
  for (var m = 0; m < months.length; m++) if (months[m].isCurrent) currentMonth = months[m];

  for (var k in byKey) {
    var grp = byKey[k];
    var recsByMonth = {};
    var car = '';
    for (var j = 0; j < grp.recs.length; j++) recsByMonth[grp.recs[j].monthIndex] = grp.recs[j];

    var monthly = [], trend = [];
    var closedBalance = 0, totalObligation = 0, totalPaid = 0;
    var cur = null;

    for (var s = 0; s < monthsBySort.length; s++) {
      var mo = monthsBySort[s];
      var rc = recsByMonth[mo.index];
      if (!rc) continue;
      if (rc.car) car = rc.car;
      var collection = rc.obligation ? Math.min(1, rc.paid / Math.abs(rc.obligation)) : 0;
      var entry = {
        label: mo.label, year: mo.year, month: mo.month, isCurrent: mo.isCurrent,
        obligation: rc.obligation, paid: rc.paid, remaining: rc.remaining,
        collection: collection, car: rc.car, lastPaymentDay: rc.lastPaymentDay
      };
      monthly.push(entry);
      trend.push({ label: mo.label, remaining: rc.remaining, isCurrent: mo.isCurrent });

      if (mo.isCurrent) {
        cur = entry;
      } else {
        closedBalance += rc.remaining;
        totalObligation += rc.obligation;
        totalPaid += rc.paid;
      }
    }

    // Глубина просрочки: подряд идущие закрытые месяцы с долгом (с конца).
    var closedChrono = monthly.filter(function (e) { return !e.isCurrent; });
    var monthsOverdue = 0;
    for (var z = closedChrono.length - 1; z >= 0; z--) {
      if (closedChrono[z].remaining < -0.5) monthsOverdue++;
      else break;
    }

    // Текущий месяц — pro-rata прогноз, в просрочку не пишем.
    var currentObligation = cur ? cur.obligation : 0;
    var currentPaid = cur ? cur.paid : 0;
    var currentRemaining = cur ? cur.remaining : 0;
    var prorataExpected = 0, currentCollection = 0;
    if (cur && currentMonth) {
      prorataExpected = Math.abs(currentObligation) * (currentMonth.daysElapsed / currentMonth.daysInMonth);
      currentCollection = prorataExpected ? Math.min(1, currentPaid / prorataExpected) : 0;
      curObSum += Math.abs(currentObligation);
      curPaidSum += currentPaid;
    }

    // Дней без оплаты — от последней фактической оплаты до сегодня.
    var lastPaymentDate = null, daysWithoutPayment = null;
    for (var s2 = monthsBySort.length - 1; s2 >= 0; s2--) {
      var mo2 = monthsBySort[s2];
      var rc2 = recsByMonth[mo2.index];
      if (rc2 && rc2.lastPaymentDay > 0) {
        lastPaymentDate = Utilities.formatDate(new Date(mo2.year, mo2.month, rc2.lastPaymentDay), tz, 'yyyy-MM-dd');
        var lpMs = Date.UTC(mo2.year, mo2.month, rc2.lastPaymentDay);
        daysWithoutPayment = Math.max(0, Math.round((todayMs - lpMs) / 86400000));
        break;
      }
    }

    var avgObligation = closedChrono.length
      ? closedChrono.reduce(function (a, e) { return a + Math.abs(e.obligation); }, 0) / closedChrono.length
      : Math.abs(currentObligation);

    // Светофор статуса.
    var status;
    if (closedBalance >= -0.5) status = 'green';
    else if (monthsOverdue >= 2 || Math.abs(closedBalance) > avgObligation) status = 'red';
    else status = 'yellow';

    // Bucket старения.
    var bucket;
    if (closedBalance >= -0.5 || monthsOverdue === 0) bucket = 'current';
    else if (monthsOverdue === 1) bucket = 'm1';
    else if (monthsOverdue <= 3) bucket = 'm23';
    else bucket = 'm3plus';

    var debt = Math.min(0, closedBalance); // отрицательное = долг
    if (closedBalance < -0.5) {
      debtorsCount++;
      grossDebt += -debt;
      aging[bucket] += -debt;
      if (monthsOverdue >= 2) debtOver2m += -debt;
    }
    netBalance += closedBalance;

    var collectionRate = totalObligation ? Math.min(1, totalPaid / Math.abs(totalObligation)) : 0;

    drivers.push({
      key: grp.key, name: grp.name, car: car,
      closedBalance: round2(closedBalance),
      currentObligation: round2(currentObligation),
      currentPaid: round2(currentPaid),
      currentRemaining: round2(currentRemaining),
      prorataExpected: round2(prorataExpected),
      currentCollection: round2(currentCollection),
      totalObligation: round2(totalObligation),
      totalPaid: round2(totalPaid),
      collectionRate: round2(collectionRate),
      monthsOverdue: monthsOverdue,
      daysWithoutPayment: daysWithoutPayment,
      lastPaymentDate: lastPaymentDate,
      status: status,
      agingBucket: bucket,
      trend: trend,
      monthly: monthly
    });
  }

  drivers.sort(function (a, b) { return a.closedBalance - b.closedBalance; }); // худшие сверху

  var totals = {
    netBalance: round2(netBalance),
    totalDebt: round2(grossDebt),
    debtorsCount: debtorsCount,
    avgDebt: debtorsCount ? round2(grossDebt / debtorsCount) : 0,
    collectionRateMonth: curObSum ? round2(Math.min(1, curPaidSum / curObSum)) : 0,
    debtOver2m: round2(debtOver2m),
    aging: {
      current: round2(aging.current),
      m1: round2(aging.m1),
      m23: round2(aging.m23),
      m3plus: round2(aging.m3plus)
    }
  };

  return {
    generatedAt: new Date().toISOString(),
    today: today.y + '-' + pad2(today.m + 1) + '-' + pad2(today.d),
    timezone: tz,
    months: months,
    currentMonthLabel: currentMonth ? currentMonth.label : null,
    totals: totals,
    drivers: drivers,
    buyoutByMonth: buyoutByMonth
  };
}

function round2(n) { return Math.round(n * 100) / 100; }
function pad2(n) { return (n < 10 ? '0' : '') + n; }
