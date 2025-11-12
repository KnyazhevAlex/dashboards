import streamlit as st
import pandas as pd
from gm_api import GMAPI
import plotly.graph_objects as go
from datetime import datetime, timezone

# === Настройки страницы ===
st.set_page_config(page_title="GM API Dashboard", layout="wide")

# === Получаем API ключ из URL ===
query_params = st.query_params
api_key = query_params.get("session_key", [None])[0] if isinstance(query_params.get("session_key"), list) else query_params.get("session_key")

if not api_key:
    st.error("❌ В ссылке не найден параметр `session_key`. Добавьте его в URL, например: ?session_key=ВАШ_ХЕШ")
    st.stop()

# === Центральный заголовок ===
st.markdown(
    """
    <h1 style='text-align: center; margin-top: -20px;'>
        🚘 Обзор автопарка
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
    st.subheader("Текущее состояние автопарка")

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
            height=420,
            annotations=[dict(
                text=f"Всего<br><b>{total}</b>",
                x=0.5, y=0.5,
                font=dict(size=20, color='#333'),
                showarrow=False
            )]
        )

        outer_left, outer_right = st.columns([1, 1])
        with outer_left:
            pie_col, legend_col = st.columns([2, 1])
            with pie_col:
                st.plotly_chart(fig, use_container_width=True)
            with legend_col:
                for lbl in status_colors:
                    color = status_colors[lbl]
                    count = counters.get(lbl, 0)
                    st.markdown(
                        f"<span style='display:flex;align-items:center'>"
                        f"<div style='width:14px;height:14px;background:{color};margin-right:8px;border-radius:3px'></div> "
                        f"{lbl}: {count}</span>",
                        unsafe_allow_html=True
                    )


