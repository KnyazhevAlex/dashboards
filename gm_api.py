import requests
from datetime import datetime, timedelta

class GMAPI:
    def __init__(self, api_key: str):
        self.base_url = "https://my.gdemoi.ru/api-v2"
        self.api_key = api_key

    def get_trackers(self):
        """Получаем список трекеров пользователя"""
        url = f"{self.base_url}/tracker/list"
        params = {"hash": self.api_key}
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_tracker_readings(self, tracker_id: int):
        """Получает показания сенсоров по определенному трекеру"""
        url = f"{self.base_url}/tracker/readings/list"
        params = {"hash": self.api_key, "tracker_id": tracker_id}
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_tracker_readings_batch(self, tracker_ids: list[int]):
        """Получает показания сенсоров сразу по нескольким трекерам"""
        url = f"{self.base_url}/tracker/readings/batch_list"
        payload = {"hash": self.api_key, "tracker_ids": tracker_ids}
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json()
    
    def get_sensor_data(self, tracker_id: int, sensor_id: int, from_ts: str, to_ts: str, raw_data: bool=False):
        """Исторические данные датчиков - сырые данные"""
        url = f"{self.base_url}/tracker/sensor/data/read"
        params = {
        "hash": self.api_key,
        "tracker_id": tracker_id,
        "sensor_id": sensor_id,
        "from": from_ts,
        "to": to_ts,
        "raw_data": str(raw_data).lower()
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_states(self, tracker_ids: list[int], list_blocked: bool=False, allow_not_exist: bool=False):
        """Текущее состояние нескольких трекеров"""
        url = f"{self.base_url}/tracker/get_states"
        payload = {
            "hash": self.api_key,
            "trackers": tracker_ids,
            "list_blocked": list_blocked,
            "allow_not_exist": allow_not_exist
        }
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_tracker_status(self, state_obj):
        """
        Определяет статус трекера по финальной логике:
        - В движении: connection_status=active и movement_status ∈ [moving, stopped]
        - Стоит: connection_status=active и movement_status=parked и ignition=False
        - Стоит с включенным зажиганием: connection_status=active и movement_status=parked и ignition=True
        - Нет координат: connection_status=idle
        - Не в сети: connection_status ∈ [offline, signal_lost, just_registered, just_replaced] или данных нет
        """
        if not state_obj:
            return "Не в сети"

        s = state_obj.get("state", state_obj)
        conn = (s.get("connection_status") or "").lower()
        move = (s.get("movement_status") or "").lower()
        ignition = s.get("ignition", False)

        # --- Не в сети ---
        offline_statuses = {"offline", "signal_lost", "just_registered", "just_replaced"}
        if conn in offline_statuses or not conn:
            return "Не в сети"

        # --- Нет координат ---
        if conn == "idle":
            return "Нет координат"

        # --- Стоит / Стоит с включенным зажиганием ---
        if conn == "active" and move == "parked":
            return "Стоит с включенным зажиганием" if ignition else "Стоит"

        # --- В движении ---
        if conn == "active" and move in ("moving", "stopped"):
            return "В движении"

        # --- fallback ---
        return "Не в сети"