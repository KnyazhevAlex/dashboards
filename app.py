import streamlit as st
import pandas as pd
from gm_api import GMAPI
import plotly.graph_objects as go
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
from concurrent.futures import ThreadPoolExecutor

def section_title(text):
    """Минималистичный заголовок секции"""
    st.markdown(
        f"<h3 style='text-align:center; margin-bottom:1rem;'>{text}</h3>",
        unsafe_allow_html=True
    )

def draw_status_card(stats, details):
    
    rows = [
        ("✅", "В порядке", "ok"),
        ("⚠️", "Скоро истекает", "expiring"),
        ("❌", "Просрочено", "expired"),
        ("🟪", "Не заполнено", "empty"),
    ]

    for emoji, label, key in rows:
        col_text, col_num, col_info = st.columns([3, 1, 1])

        with col_text:
            st.markdown(f"{emoji} **{label}:**")

        with col_num:
            st.markdown(f"**{stats[key]}**")

        # popover со списками
        with col_info:
            if key != "ok" and details[key]:
                with st.popover("ℹ️"):
                    for item in details[key]:
                        st.markdown(f"• {item}")
            else:
                st.markdown("<div class='status-row'></div>", unsafe_allow_html=True)

# === Кэшируемые функции загрузки данных ===

@st.cache_data(ttl=300, show_spinner=False)
def load_trackers(api_key):
    gm = GMAPI(api_key)
    return gm.get_trackers()

@st.cache_data(ttl=300, show_spinner=False)
def load_states(api_key, tracker_ids):
    gm = GMAPI(api_key)
    return gm.get_states(tracker_ids, list_blocked=True, allow_not_exist=True)

@st.cache_data(ttl=300, show_spinner=False)
def load_employees(api_key):
    gm = GMAPI(api_key)
    return gm.get_employees()

@st.cache_data(ttl=300, show_spinner=False)
def load_vehicles(api_key):
    gm = GMAPI(api_key)
    return gm.get_vehicles()

@st.cache_data(ttl=600, show_spinner=False) # Кэш на 10 минут для тяжелых поездок
def load_trips_stats(api_key, tracker_ids, from_dt, to_dt):
    gm = GMAPI(api_key)
    return gm.get_trips_parallel(tracker_ids, from_dt, to_dt)

@st.cache_data(ttl=600, show_spinner=False)
def load_fuel_data(api_key, tracker_ids, from_dt, to_dt):
    gm = GMAPI(api_key)
    try:
        # 1. Генерация
        gen_resp = gm.generate_fuel_report(tracker_ids, from_dt, to_dt)
        report_id = gen_resp.get("id")
        
        if not report_id:
            return {"error": "Не удалось получить ID отчета"}
            
        # 2. Ожидание (поллинг)
        for _ in range(30): # Макс 60 секунд
            status = gm.get_report_status(report_id)
            if status.get("success") and status.get("percent_ready") == 100:
                break
            time.sleep(2)
        else:
            return {"error": "Таймаут создания отчета"}
            
        # 3. Скачивание
        return gm.retrieve_report(report_id)
        
    except Exception as e:
        return {"error": str(e)}

def process_driver_licenses(employees):
    today = datetime.now().date()
    soon_limit = today + timedelta(days=30)

    stats = {"ok": 0, "expiring": 0, "expired": 0, "empty": 0}
    details = {"ok": [], "expiring": [], "expired": [], "empty": []}

    for emp in employees:
        name = f"{emp.get('first_name','')} {emp.get('last_name','')}".strip()
        valid_till = emp.get("driver_license_valid_till")

        if not valid_till:
            stats["empty"] += 1
            details["empty"].append(name)
            continue

        try:
            # Пытаемся распарсить дату
            dt = datetime.strptime(valid_till, "%Y-%m-%d").date()
        except ValueError:
            stats["empty"] += 1
            details["empty"].append(name)
            continue

        if dt < today:
            stats["expired"] += 1
            details["expired"].append(name)
        elif today <= dt < soon_limit:
            stats["expiring"] += 1
            details["expiring"].append(name)
        else:
            stats["ok"] += 1
            details["ok"].append(name)

    return stats, details

def process_insurance(vehicles):
    today = datetime.now().date()
    soon_limit = today + timedelta(days=30)

    stats = {"ok": 0, "expiring": 0, "expired": 0, "empty": 0}
    details = {"ok": [], "expiring": [], "expired": [], "empty": []}

    for v in vehicles:
        name = v.get("label", "Без названия")
        reg = v.get("reg_number", "")
        item = f"{name} — {reg}" if reg else name

        osago = v.get("liability_insurance_valid_till")
        kasko = v.get("free_insurance_valid_till")
        valid_till = osago or kasko

        if not valid_till:
            stats["empty"] += 1
            details["empty"].append(item)
            continue

        try:
            dt = datetime.strptime(valid_till, "%Y-%m-%d").date()
        except ValueError:
            stats["empty"] += 1
            details["empty"].append(item)
            continue

        if dt < today:
            stats["expired"] += 1
            details["expired"].append(item)
        elif today <= dt < soon_limit:
            stats["expiring"] += 1
            details["expiring"].append(item)
        else:
            stats["ok"] += 1
            details["ok"].append(item)

    return stats, details


# === Настройки страницы ===
st.set_page_config(page_title="GM API Dashboard", layout="wide")

# === Получаем API ключ из URL ===
query_params = st.query_params
api_key = query_params.get("session_key", [None])[0] if isinstance(query_params.get("session_key"), list) else query_params.get("session_key")

if not api_key:
    st.error("❌ В ссылке не найден параметр `session_key`. Добавьте его в URL, например: ?session_key=hash")
    st.stop()

# === Центральный заголовок ===
st.markdown(
    """
    <h1 style='text-align: center; margin-top: -20px;'>
        Обзор автопарка
    </h1>
    """,
    unsafe_allow_html=True
)

# === Подключаемся к API ===
gm = GMAPI(api_key)

# === Получаем список трекеров (Кэшировано) ===
try:
    data = load_trackers(api_key)
except Exception as e:
    st.error(f"Ошибка при загрузке списка трекеров: {e}")
    st.stop()

if "list" not in data:
    st.error("Ответ API не содержит ключ 'list'")
    st.stop()

trackers = data["list"]
tracker_ids = [int(t["id"]) for t in trackers]

# === Блок 1: Верхняя часть (Статусы, Права, Страховка) - Грузится быстро ===

# Получаем состояния (Кэшировано)
try:
    states_response = load_states(api_key, tracker_ids)
    states = states_response.get("states", {})
except Exception as e:
    st.error(f"Ошибка при получении состояний трекеров: {e}")
    states = {}

# Инициализация счётчиков
counters = {
    "Едет": 0,
    "Стоит": 0,
    "Холостой ход": 0,
    "Нет координат": 0,
    "Не в сети": 0
}

status_norm_map = {
    "В движении": "Едет",
    "Едет": "Едет",
    "Стоит": "Стоит",
    "Стоит с включенным зажиганием": "Холостой ход",
    "Холостой ход": "Холостой ход",
    "Нет координат": "Нет координат",
    "Не в сети": "Не в сети",
}

for tid, state in states.items():
    raw_status = gm.get_tracker_status(state)
    canon = status_norm_map.get(raw_status, raw_status)
    counters[canon] = counters.get(canon, 0) + 1

# Визуализация пирога
labels, values = [], []
for k, v in counters.items():
    if v > 0:
        labels.append(k)
        values.append(v)

col_left, col_center, col_right = st.columns([1, 1, 1])

with col_left:
    with st.container(border=True):
        section_title("Текущее состояние автопарка")
        if not values:
            st.info("Нет данных")
        else:
            # Мягкая минималистичная палитра
            status_colors = {
                "Едет": "#10b981",
                "Стоит": "#3b82f6",
                "Холостой ход": "#f59e0b",
                "Нет координат": "#9ca3af",
                "Не в сети": "#ef4444"
            }
            colors = [status_colors.get(lbl, "#CCCCCC") for lbl in labels]

            fig = go.Figure(go.Pie(
                labels=labels,
                values=values,
                hole=0.55,
                marker=dict(colors=colors),
                sort=False,
                textinfo='percent',
                hoverinfo='label+value+percent',
                hovertemplate='%{label}: %{value} устройств (%{percent})<extra></extra>'
            ))

            total = sum(values)
            fleet_total = total
            fig.update_traces(textposition='inside', insidetextorientation='radial', pull=[0.02]*len(labels))
            fig.update_layout(
                showlegend=False,
                margin=dict(t=20, b=10, l=10, r=10),
                height=320,
                annotations=[dict(
                    text=f"Всего<br><b>{total}</b>",
                    x=0.5, y=0.5,
                    font=dict(size=20, color='#333'),
                    showarrow=False
                )]
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

with col_center:
    with st.container(border=True):
        section_title("Водительские удостоверения")
        try:
            employees_data = load_employees(api_key)
            employees = employees_data.get("list", [])
        except Exception as e:
            st.error(f"Ошибка: {e}")
            employees = []
        
        if not employees:
            st.warning("⚠️ Данные отсутствуют - заполните раздел Водители")
        else:
            vu_stats, vu_details = process_driver_licenses(employees)
            draw_status_card(vu_stats, vu_details)

with col_right:
    with st.container(border=True):
        section_title("Страховка")
        try:
            vehicles_data = load_vehicles(api_key)
            vehicles = vehicles_data.get("list", [])
        except Exception as e:
            st.error(f"Ошибка: {e}")
            vehicles = []

        if not vehicles:
            st.warning("⚠️ Данные отсутствуют - заполните раздел Транспорт")
        else:
            insurance_stats, insurance_details = process_insurance(vehicles)
            draw_status_card(insurance_stats, insurance_details)


# === БЛОК 2: Тяжелые данные (Поездки) ===

# Контейнер для метрик, чтобы они появились после загрузки
metrics_container = st.container()

# Функция для расчета дат с учетом таймзоны
def get_day_range_ts(date_obj, tz_name="Europe/Moscow"):
    tz = ZoneInfo(tz_name)
    # Начало дня в локальной зоне
    start_local = datetime.combine(date_obj, datetime.min.time()).replace(tzinfo=tz)
    # Конец дня
    end_local = start_local + timedelta(days=1) - timedelta(seconds=1)
    
    # API требует строки в формате "YYYY-MM-DD HH:MM:SS"
    fmt = "%Y-%m-%d %H:%M:%S"
    return start_local.strftime(fmt), end_local.strftime(fmt)

# Локальные даты
tz_msk = ZoneInfo("Europe/Moscow")
now_msk = datetime.now(tz_msk)
today = now_msk.date()
yesterday = today - timedelta(days=1)
day_before = today - timedelta(days=2)

# Получаем таймстампы для запроса (сразу за 2 дня)
from_dt, _ = get_day_range_ts(day_before)
_, to_dt = get_day_range_ts(yesterday)

# === Оптимизация: Фильтруем трекеры, которые давно не обновлялись ===
active_tracker_ids = []
# Парсим дату начала периода для сравнения
try:
    period_start_dt = datetime.strptime(from_dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Moscow"))
except:
    period_start_dt = None

if period_start_dt:
    for tid in tracker_ids:
        state_obj = states.get(tid, {})
        # last_update может быть в state_obj или внутри state_obj["state"]
        # Обычно это поле "last_update" (строка)
        s = state_obj.get("state", state_obj)
        last_upd_str = s.get("last_update")
        
        if not last_upd_str:
            # Если нет даты обновления, на всякий случай берем
            active_tracker_ids.append(tid)
            continue
            
        try:
            # Формат обычно "YYYY-MM-DD HH:MM:SS"
            # Предполагаем, что API возвращает время в UTC или локальное. 
            # Для надежности просто сравниваем строки (YYYY-MM-DD) или парсим
            lu_dt = datetime.strptime(last_upd_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Moscow"))
            
            # Если последнее обновление было ПОЗЖЕ начала периода (с запасом 1 день), берем
            # Или если оно было ХОТЯ БЫ в этом периоде
            if lu_dt >= period_start_dt - timedelta(days=1):
                active_tracker_ids.append(tid)
        except:
            # Если ошибка парсинга, берем
            active_tracker_ids.append(tid)
else:
    active_tracker_ids = tracker_ids

# === ЗАГРУЗКА ПОЕЗДОК С ИНДИКАЦИЕЙ ===
# Используем st.status для красивого отображения процесса
with st.status(f"Загрузка истории поездок ({len(active_tracker_ids)} из {len(tracker_ids)} активных)...", expanded=True) as status:
    st.write("Подключение к API...")
    # Загружаем данные (Кэшировано!)
    try:
        two_days_trips = load_trips_stats(api_key, active_tracker_ids, from_dt, to_dt)
        status.update(label="Данные успешно загружены!", state="complete", expanded=False)
    except Exception as e:
        status.update(label="Ошибка загрузки данных!", state="error")
        st.error(f"Не удалось получить данные о поездках: {e}")
        st.stop()

# --- Обработка данных (быстро, в памяти) ---
yesterday_str = yesterday.strftime("%Y-%m-%d")
day_before_str = day_before.strftime("%Y-%m-%d")

yesterday_trips = []
day_before_trips = []

for item in two_days_trips:
    tid = item["id"]
    trips = item["trips"]

    y_list = []
    db_list = []

    for tr in trips:
        start_str = tr.get("start_date") # "2025-11-16 10:00:00"
        if not start_str:
            continue
        
        # API возвращает дату как строку, берем первые 10 символов (YYYY-MM-DD)
        trip_date = start_str[:10]

        if trip_date == yesterday_str:
            y_list.append(tr)
        elif trip_date == day_before_str:
            db_list.append(tr)

    yesterday_trips.append({"id": tid, "trips": y_list})
    day_before_trips.append({"id": tid, "trips": db_list})

# --- KPI расчёты ---
active_count = sum(1 for t in yesterday_trips if len(t["trips"]) > 0)
prev_active_count = sum(1 for t in day_before_trips if len(t["trips"]) > 0)

total_distance = 0.0
total_move_time = 0
total_idle_time = 0

for item in yesterday_trips:
    for tr in item["trips"]:
        total_distance += float(tr.get("length", 0) or 0)
        
        # Расчет времени в пути (через разницу дат)
        s_str = tr.get("start_date")
        e_str = tr.get("end_date")
        if s_str and e_str:
            try:
                s_dt = datetime.strptime(s_str, "%Y-%m-%d %H:%M:%S")
                e_dt = datetime.strptime(e_str, "%Y-%m-%d %H:%M:%S")
                dur = (e_dt - s_dt).total_seconds()
                if dur > 0:
                    total_move_time += dur
            except:
                # Если не удалось распарсить, пробуем взять поле duration, если оно есть
                total_move_time += (tr.get("duration") or 0)
        else:
             total_move_time += (tr.get("duration") or 0)

        # Холостой ход (idle_duration)
        idle_sec = tr.get("idle_duration") or 0
        if isinstance(idle_sec, (int, float)):
            total_idle_time += idle_sec

def fmt_time(seconds):
    if not seconds or seconds <= 0:
        return "0 ч"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h} ч {m} мин"

avg_drive_time = fmt_time(total_move_time / active_count if active_count else 0)
idle_time_fmt = fmt_time(total_idle_time)

# Расчет среднего пробега на авто
avg_mileage = total_distance / active_count if active_count else 0

# Тренд
if active_count > prev_active_count:
    trend = "↑ Больше, чем позавчера"
    trend_color = "#3CB371"
    trend_val = active_count - prev_active_count
    trend_sign = "+" if trend_val > 0 else ""
    trend_text = f"{trend_sign}{trend_val}"
elif active_count < prev_active_count:
    trend = "↓ Меньше, чем позавчера"
    trend_color = "#E74C3C"
    trend_val = active_count - prev_active_count
    trend_text = f"{trend_val}"
else:
    trend = "→ Без изменений"
    trend_color = "#888"
    trend_text = "0"


# === Вывод метрик ===
st.write("")  # Добавляем пространство

with metrics_container:
    col_a, col_b, col_c = st.columns([1, 1, 1])
    
    with col_a:
        with st.container(border=True):
            section_title("Активность за период")
            st.metric(
                label="Активных ТС (вчера)",
                value=f"{active_count} / {len(trackers)}",
                delta=trend_text,
                help="Количество транспортных средств, совершивших поездки"
            )
            st.caption(trend)

    with col_b:
        with st.container(border=True):
            section_title("Пробег и движение")
            st.metric(
                label="Общий пробег (вчера)",
                value=f"{total_distance:,.1f} км",
                help="Суммарный пробег всех активных ТС"
            )
            st.caption(f"⏱️ Среднее время в пути: **{avg_drive_time}**")
            st.caption(f"📏 Средний пробег на авто: **{avg_mileage:.1f} км**")

    with col_c:
        with st.container(border=True):
            section_title("Холостой ход")
            st.metric(
                label="Суммарный холостой ход (вчера)",
                value=idle_time_fmt,
                help="Время работы двигателя без движения"
            )

# === БЛОК 3: Топливо (Отчеты) ===

# Загружаем данные по топливу
fuel_container = st.container()

with st.status("Загрузка данных по топливу (сравнение с позавчера)...", expanded=False) as status:
    # Даты для вчера и позавчера
    f_start_y, f_end_y = get_day_range_ts(yesterday)
    f_start_db, f_end_db = get_day_range_ts(day_before)
    
    # Проверка на наличие активных трекеров
    if not active_tracker_ids:
        status.update(label="Нет активных трекеров для отчета", state="complete")
        fuel_report_y = None
        fuel_report_db = None
    else:
        # Запускаем параллельно
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_y = executor.submit(load_fuel_data, api_key, active_tracker_ids, f_start_y, f_end_y)
            future_db = executor.submit(load_fuel_data, api_key, active_tracker_ids, f_start_db, f_end_db)
            
            fuel_data_y = future_y.result()
            fuel_data_db = future_db.result()
        
        # Проверяем ошибки (хотя бы за вчера должно загрузиться)
        if "error" in fuel_data_y:
            status.update(label="Ошибка загрузки топлива (Вчера)", state="error")
            st.error(f"Ошибка (Вчера): {fuel_data_y['error']}")
            fuel_report_y = None
        else:
            fuel_report_y = fuel_data_y

        if "error" in fuel_data_db:
            # Не критично, просто не будет тренда
            fuel_report_db = None
        else:
            fuel_report_db = fuel_data_db
            
    status.update(label="Данные по топливу загружены", state="complete")

if fuel_report_y and fuel_report_y.get("success"):
    try:
        # Функция парсинга
        def parse_fuel_report(rep):
            if not rep or not rep.get("success"):
                return {}
            try:
                sheet = rep["report"]["sheets"][0]
                section = sheet["sections"][0]
                data_block = section["data"][0]
                total_row = data_block.get("total", {})
                
                def get_val(obj, key):
                    item = obj.get(key, {})
                    return item.get("raw", 0) if isinstance(item.get("raw"), (int, float)) else 0
                
                return {
                    "fillings_count": get_val(total_row, "fillingsCount"),
                    "fillings_vol": get_val(total_row, "fillingsVolume"),
                    "drains_count": get_val(total_row, "drainsCount"),
                    "drains_vol": get_val(total_row, "drainsVolume"),
                    "consumed": get_val(total_row, "consumed")
                }
            except:
                return {}

        data_y = parse_fuel_report(fuel_report_y)
        data_db = parse_fuel_report(fuel_report_db)
        
        # Данные за вчера
        fillings_vol = data_y.get("fillings_vol", 0)
        fillings_count = data_y.get("fillings_count", 0)
        drains_vol = data_y.get("drains_vol", 0)
        drains_count = data_y.get("drains_count", 0)
        consumed = data_y.get("consumed", 0)
        
        # Helper for trend formatting
        def fmt_trend(val, suffix=""):
            if abs(val) < 0.1:
                return None 
            return f"{val:+.1f}{suffix}"

        # Тренд (Вчера - Позавчера)
        if data_db:
            # Заправлено
            fillings_vol_db = data_db.get("fillings_vol", 0)
            trend_val = fillings_vol - fillings_vol_db
            trend_str = fmt_trend(trend_val, " л")
            
            # Потрачено
            consumed_db = data_db.get("consumed", 0)
            consumed_trend_val = consumed - consumed_db
            consumed_trend_str = fmt_trend(consumed_trend_val, " л")
            
            # Слито
            drains_vol_db = data_db.get("drains_vol", 0)
            drains_trend_val = drains_vol - drains_vol_db
            drains_trend_str = fmt_trend(drains_trend_val, " л")
            
            # Процент потерь
            if fillings_vol_db > 0:
                loss_pct_db = (drains_vol_db / fillings_vol_db) * 100
            else:
                loss_pct_db = 0
            
            # Расчет потерь за вчера (нужен для тренда)
            if fillings_vol > 0:
                loss_pct = (drains_vol / fillings_vol) * 100
            else:
                loss_pct = 0

            loss_pct_trend_val = loss_pct - loss_pct_db
            loss_pct_trend_str = fmt_trend(loss_pct_trend_val, "%")
            
        else:
            trend_str = None
            consumed_trend_str = None
            drains_trend_str = None
            loss_pct_trend_str = None
            
            # Расчет потерь за вчера (если нет данных за позавчера, все равно нужно посчитать для текущего дня)
            if fillings_vol > 0:
                loss_pct = (drains_vol / fillings_vol) * 100
            else:
                loss_pct = 0
            
        # Визуализация
        with fuel_container:
            st.write("")  # Добавляем пространство
            
            with st.container(border=True):
                section_title("Топливо (Вчера)")
                
                # Поле для ввода цены топлива
                fuel_price = st.number_input(
                    "Цена топлива (₽/литр)",
                    min_value=0.0,
                    max_value=200.0,
                    value=63.0,
                    step=0.5,
                    help="Введите актуальную цену топлива для расчета финансовых показателей",
                    key="fuel_price_input"
                )
                
                # Пересчет стоимости на основе введенной цены
                fillings_cost = fillings_vol * fuel_price
                consumed_cost = consumed * fuel_price
                drains_cost = drains_vol * fuel_price
                
                st.write("")  # Пространство перед метриками
                
                c1, c2, c3, c4 = st.columns(4)
                
                with c1:
                    st.metric("Заправлено", f"{fillings_vol:.1f} л", delta=trend_str, help="Сравнение с позавчерашним днем")
                    st.caption(f"💰 {fillings_cost:,.0f} ₽")
                    st.caption(f"⛽ Заправок: {fillings_count}")
                    
                with c2:
                    st.metric("Потрачено", f"{consumed:.1f} л", delta=consumed_trend_str, help="Сравнение с позавчерашним днем")
                    st.caption(f"💰 {consumed_cost:,.0f} ₽")
                    
                with c3:
                    st.metric("Слито (Потери)", f"{drains_vol:.1f} л", delta=drains_trend_str, delta_color="inverse", help="Сравнение с позавчерашним днем")
                    st.caption(f"💰 {drains_cost:,.0f} ₽")
                    st.caption(f"🚨 Сливов: {drains_count}")
                    
                with c4:
                    st.metric("Процент потерь", f"{loss_pct:.1f}%", delta=loss_pct_trend_str, delta_color="inverse", help="Отношение объема сливов к объему заправок")

                
    except Exception as e:
        st.error(f"Ошибка обработки отчета: {e}")

