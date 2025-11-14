import streamlit as st
import pandas as pd
from gm_api import GMAPI
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta

def section_title(text, top_offset=-10):
    #Разметка заголовков
    st.markdown(
        f"""
        <div style='margin-top:{top_offset}px; margin-bottom:10px;'>
            <h3 style="margin: 0; text-align:center; font-weight:600;">{text}</h3>
        </div>
        """,
        unsafe_allow_html=True
    )

def draw_status_card(title, stats):
 
    st.markdown(
        f"""
        <div style='font-size:17px; line-height: 1.6; margin-left:5px;'>
            <span style='margin-right:6px;'>✅</span> <b>В порядке:</b> {stats['ok']}<br>
            <span style='margin-right:6px;'>⚠️</span> <b>Скоро истекает:</b> {stats['expiring']}<br>
            <span style='margin-right:6px;'>❌</span> <b>Просрочено:</b> {stats['expired']}<br>
            <span style='margin-right:6px;'>🟪</span> <b>Не заполнено:</b> {stats['empty']}
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown("</div>", unsafe_allow_html=True)

def process_driver_licenses(employees):
    today = datetime.now().date()
    soon_limit = today + timedelta(days=30)

    stats = {"ok": 0, "expiring": 0, "expired": 0, "empty": 0}

    for emp in employees:
        valid_till = emp.get("driver_license_valid_till")

        if not valid_till:
            stats["empty"] += 1
            continue

        try:
            dt = datetime.strptime(valid_till, "%Y-%m-%d").date()
        except:
            stats["empty"] += 1
            continue

        if dt < today:
            stats["expired"] += 1
        elif today <= dt < soon_limit:
            stats["expiring"] += 1
        else:
            stats["ok"] += 1

    return stats

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

# === Получаем список трекеров ===
data = gm.get_trackers()

if "list" not in data:
    st.error("Ответ API не содержит ключ 'list'")
else:
    trackers = data["list"]

    # === Блок: Автоматическая статистика по статусам ===
    # Получаем ID всех трекеров
    tracker_ids = [int(t["id"]) for t in trackers]

    # Получаем их текущее состояние через API
    try:
        states_response = gm.get_states(tracker_ids, list_blocked=True, allow_not_exist=True)
        states = states_response.get("states", {})
    except Exception as e:
        st.error(f"Ошибка при получении состояний трекеров: {e}")
        states_response = {}
        states = {}

    # Инициализация счётчиков (канонические статусы)
    counters = {
        "Едет": 0,
        "Стоит": 0,
        "Холостой ход": 0,
        "Нет координат": 0,
        "Не в сети": 0
    }

    # Нормализация вариантов статусов
    status_norm_map = {
        "В движении": "Едет",
        "Едет": "Едет",
        "Стоит": "Стоит",
        "Стоит с включенным зажиганием": "Холостой ход",
        "Холостой ход": "Холостой ход",
        "Нет координат": "Нет координат",
        "Не в сети": "Не в сети",
    }

    # Перебираем все состояния
    for tid, state in states.items():
        raw_status = gm.get_tracker_status(state)
        canon = status_norm_map.get(raw_status, raw_status)
        counters[canon] = counters.get(canon, 0) + 1

    # === Визуализация пирога ===
    labels, values = [], []
    for k, v in counters.items():
        if v > 0:
            labels.append(k)
            values.append(v)

    if not values:
        st.info("Нет данных по статусам устройств для отображения диаграммы.")
    else:
        status_colors = {
            "Едет": "#3CB371",
            "Стоит": "#1E90FF",
            "Холостой ход": "#FFD966",
            "Нет координат": "#A9A9A9",
            "Не в сети": "#E74C3C"
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

        col_left, col_center, col_right = st.columns([1, 1, 1], border=True)
        with col_left:
                section_title("Текущее состояние автопарка")
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        with col_center:
            # === Водительские удостоверения ===
            section_title("Водительские удостоверения")
            try:
                employees = gm.get_employees().get("list", [])
            except Exception as e:
                st.error(f"Ошибка при загрузке сотрудников: {e}")
                employees = []
            # --- Если водителей нет ---
            if not employees:
                st.markdown("""
                    <div style="
                        padding: 15px 20px;
                        border-radius: 10px;
                        background: #ffffff;
                        border: 1px solid #ddd;
                        box-shadow: 0px 1px 3px rgba(0,0,0,0.06);
                        font-size: 17px;">
                        ⚠️ Данные отсутствуют — заполните раздел «Водители»
                    </div>
                """, unsafe_allow_html=True)

            else:
                vu_stats = process_driver_licenses(employees)
                draw_status_card("Водительские удостоверения", vu_stats)
        with col_right:
            section_title("Страховка")
