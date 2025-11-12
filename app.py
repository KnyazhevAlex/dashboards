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

# === Подключаемся к API ===
gm = GMAPI(api_key)

# === Получаем список трекеров ===
data = gm.get_trackers()

if "list" not in data:
    st.error("Ответ API не содержит ключ 'list'")
else:
    trackers = data["list"]

    # === Блок: Автоматическая статистика по статусам ===
    st.subheader("Статусы транспортных средств")

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

    # === Карточки статусов ===
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("🟢 В движении", counters.get("Едет", 0))
    col2.metric("🟠 Стоит", counters.get("Стоит", 0))
    col3.metric("🟡 Стоит с включенным зажиганием", counters.get("Холостой ход", 0))
    col4.metric("⚪ Нет координат", counters.get("Нет координат", 0))
    col5.metric("⚫ Не в сети", counters.get("Не в сети", 0))

    # === Отладка ===
    with st.expander("🧪 Отладка статусов (сырые данные по трекерам)"):
        rows = []
        states_map = states_response.get("states") or {}

        for tid, s in states_map.items():
            gps = (s or {}).get("gps") or {}
            conn = (s or {}).get("connection") or {}
            mov = (s or {}).get("movement") or {}
            inputs = (s or {}).get("inputs") or []

            try:
                name = next((t["label"] for t in trackers if int(t["id"]) == int(tid)), f"Трекер {tid}")
            except Exception:
                name = f"Трекер {tid}"

            updated_str = gps.get("updated")
            age_min = None
            if updated_str:
                try:
                    dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                    age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
                except Exception:
                    age_min = None

            ignition = None
            if isinstance(inputs, list):
                for i in inputs:
                    if isinstance(i, dict) and i.get("type") == "ignition":
                        ignition = bool(i.get("value"))
                        break

            computed_status = gm.get_tracker_status(s)
            offline_rule = (not conn.get("online", True)) or (age_min is not None and age_min > 60)

            rows.append({
                "ID": int(tid) if str(tid).isdigit() else tid,
                "Название": name,
                "online": conn.get("online", None),
                "updated": updated_str,
                "age_min": None if age_min is None else round(age_min, 1),
                "movement.status": mov.get("status"),
                "speed": gps.get("speed"),
                "ignition": ignition,
                "computed_status": computed_status,
                "offline_rule": offline_rule,
            })

        dbg_df = pd.DataFrame(rows)
        if not dbg_df.empty:
            st.dataframe(
                dbg_df.sort_values(["offline_rule", "computed_status", "age_min"], ascending=[False, True, True]),
                use_container_width=True
            )

    # === Таблица трекеров ===
    simplified = []
    for t in trackers:
        src = t.get("source", {})
        simplified.append({
            "ID": t.get("id"),
            "Название": t.get("label"),
            "Модель": src.get("model"),
            "IMEI / Device ID": src.get("device_id"),
            "Телефон": src.get("phone"),
            "Дата создания": t.get("creation_date"),
            "Окончание тарифа": t.get("tariff_end_date"),
            "Статус (заблокирован)": "Да" if src.get("blocked") else "Нет"
        })

    df = pd.DataFrame(simplified)
    st.subheader("📄 Список трекеров")
    st.dataframe(df, use_container_width=True)

    # === Блок выбора трекера ===
    selected = st.selectbox("Выберите трекер:", df["ID"])

    # === Показания сенсоров ===
    if st.button("📟 Показать текущие показания сенсоров"):
        readings = gm.get_tracker_readings(selected)
        st.subheader("Текущие показания сенсоров")

        if "inputs" in readings and readings["inputs"]:
            df_inputs = pd.DataFrame(readings["inputs"])
            df_inputs = df_inputs[["label", "type", "value", "units_type", "update_time"]]
            df_inputs.columns = ["Название", "Тип", "Значение", "Единицы", "Время обновления"]
            st.dataframe(df_inputs)
        else:
            st.warning("⚠️ В ответе нет данных по сенсорам (inputs). Проверь, что устройство онлайн.")

    # === Уровень топлива ===
    st.subheader("⛽ Уровень топлива по всем трекерам")
    tracker_ids = [t.get("id") for t in trackers]
    batch_data = gm.get_tracker_readings_batch(tracker_ids)

    fuel_data = []
    if "result" in batch_data:
        for tracker_id, tracker_info in batch_data["result"].items():
            tracker_id = int(tracker_id)
            tracker_name = next((t["label"] for t in trackers if t["id"] == tracker_id), f"Трекер {tracker_id}")

            for sensor in tracker_info.get("inputs", []):
                if sensor.get("type") == "fuel":
                    val = sensor.get("value", 0)
                    min_val = sensor.get("min_value", 0)
                    max_val = sensor.get("max_value", 100)
                    fuel_data.append({
                        "id": tracker_id,
                        "name": tracker_name,
                        "value": val,
                        "min": min_val,
                        "max": max_val
                    })

    if fuel_data:
        total = len(fuel_data)
        cols_per_row = 6
        for start in range(0, total, cols_per_row):
            end = min(start + cols_per_row, total)
            row_items = fuel_data[start:end]
            cols = st.columns(len(row_items))
            for i, item in enumerate(row_items):
                val = item["value"]
                min_val = item["min"]
                max_val = item["max"]
                percent = (val - min_val) / (max_val - min_val) * 100 if max_val > min_val else 0
                color_steps = [
                    {"range": [0, 10], "color": "#E74C3C"},
                    {"range": [10, 25], "color": "#F1C40F"},
                    {"range": [25, 100], "color": "#2ECC71"},
                ]
                fig = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=percent,
                    number={'suffix': "%"},
                    title={'text': f"{item['name']}<br>{val:.1f} л"},
                    gauge={
                        'axis': {'range': [0, 100]},
                        'bar': {'color': "black", 'thickness': 0.3},
                        'steps': color_steps,
                        'threshold': {'line': {'color': "black", 'width': 3}, 'thickness': 0.8, 'value': percent}
                    }
                ))
                fig.update_layout(margin=dict(t=70, b=20, l=10, r=10), height=280)
                cols[i].plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Не удалось получить данные по топливу ни от одного трекера.")
