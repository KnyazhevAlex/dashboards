import requests
from datetime import datetime, timedelta
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    
    def get_employees(self):
        """Получаем список сотрудников / водителей"""
        url = f"{self.base_url}/employee/list"
        params = {"hash": self.api_key}
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_vehicles(self):
        """Получаем список ТС для ОСАГО/КАСКО"""
        url = f"{self.base_url}/vehicle/list"
        params = {"hash": self.api_key}
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_trips(self, tracker_id: int, from_dt: str, to_dt: str):
        """Поездки трекера за период (POST /track/list)"""
        url = f"{self.base_url}/track/list"
        payload = {
            "hash": self.api_key,
            "tracker_id": tracker_id,
            "from": from_dt,
            "to": to_dt,
            "filter": False,
            "count_events": True
        }
        # Используем POST и json=payload
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    
    

    def get_trips_parallel(self, tracker_ids: list[int], from_dt: str, to_dt: str, max_workers: int = 25):
        """
        Параллельное получение поездок по трекерам.
        Возвращает список словарей: { "id": tracker_id, "trips": [...], "error": str|None }
        """
        results = []

        def fetch_one(tid):
            # Простая логика повторных попыток (retries)
            attempts = 3
            for attempt in range(attempts):
                try:
                    data = self.get_trips(tid, from_dt, to_dt)
                    return {"id": tid, "trips": data.get("list", []), "error": None}
                except Exception as e:
                    if attempt == attempts - 1:
                        logger.error(f"Failed to fetch trips for tracker {tid}: {e}")
                        return {"id": tid, "trips": [], "error": str(e)}
                    time.sleep(1) # Пауза перед повтором

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {executor.submit(fetch_one, tid): tid for tid in tracker_ids}
            for future in as_completed(future_to_id):
                results.append(future.result())

        return results

    # === Работа с отчетами (Fuel) ===

    def _post_with_retry(self, url: str, payload: dict, max_retries: int = 5):
        """Выполняет POST запрос с повторными попытками при ошибке 429."""
        # Небольшая задержка перед каждым запросом на генерацию, 
        # чтобы избежать пачек одновременных вызовов
        time.sleep(1.5)
        
        for attempt in range(max_retries):
            try:
                response = requests.post(url, json=payload, timeout=30)
                if response.status_code == 429:
                    wait_time = (2 ** attempt) + 1
                    logger.warning(f"Rate limit hit (429). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1
                    time.sleep(wait_time)
                    continue
                raise e
        return None

    def generate_fuel_report(self, tracker_ids: list[int], from_dt: str, to_dt: str):
        """
        Инициирует создание отчета по топливу.
        from_dt, to_dt: строки формата "YYYY-MM-DD HH:MM:SS"
        """
        url = f"{self.base_url}/report/tracker/generate"
        
        # Параметры плагина (на основе примера пользователя)
        plugin_params = {
            "show_seconds": False,
            "plugin_id": 10, # Отчет по расходу топлива
            "graph_type": "mileage",
            "detailed_by_dates": True,
            "include_summary_sheet": True,
            "include_summary_sheet_only": True, # Нам нужны только итоги
            "use_ignition_data_for_consumption": True,
            "include_mileage_plot": False,
            "filter": True,
            "include_speed_plot": False,
            "smoothing": True,
            "surge_filter": True,
            "surge_filter_threshold": 0.01,
            "speed_filter": False,
            "speed_filter_threshold": 10
        }

        # Фильтр по времени (весь день)
        time_filter = {
            "from": "00:00:00",
            "to": "23:59:59",
            "weekdays": [1, 2, 3, 4, 5, 6, 7]
        }

        payload = {
            "hash": self.api_key,
            "title": "Fuel Report",
            "trackers": tracker_ids,
            "from": from_dt,
            "to": to_dt,
            "time_filter": time_filter,
            "plugin": plugin_params,
            "locale": "ru"
        }

        return self._post_with_retry(url, payload)

    def get_report_status(self, report_id: int):
        """Проверяет готовность отчета"""
        url = f"{self.base_url}/report/tracker/status"
        params = {
            "hash": self.api_key,
            "report_id": report_id,
            "locale": "ru"
        }
        # Для статуса тоже добавим легкую обработку 429, так как поллинг бывает частым
        for attempt in range(3):
            try:
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 429:
                    time.sleep(2)
                    continue
                response.raise_for_status()
                return response.json()
            except:
                if attempt == 2: raise
                time.sleep(1)

    def retrieve_report(self, report_id: int):
        """Скачивает готовый отчет"""
        url = f"{self.base_url}/report/tracker/retrieve"
        params = {
            "hash": self.api_key,
            "report_id": report_id
        }
        # Аналогично для скачивания
        for attempt in range(3):
            try:
                response = requests.get(url, params=params, timeout=20)
                if response.status_code == 429:
                    time.sleep(2)
                    continue
                response.raise_for_status()
                return response.json()
            except:
                if attempt == 2: raise
                time.sleep(1)


    def generate_trip_report(self, tracker_ids: list[int], from_dt: str, to_dt: str):
        """
        Генерация отчета по поездкам (plugin_id=4).
        """
        url = f"{self.base_url}/report/tracker/generate"
        
        plugin_params = {
            "plugin_id": 4,
            "include_summary_sheet_only": False,
            "split": False,
            "show_coordinates": False,
            "show_idle_duration": False,
            "show_seconds": False
        }

        # Фильтр по времени (весь день)
        time_filter = {
            "from": "00:00:00",
            "to": "23:59:59",
            "weekdays": [1, 2, 3, 4, 5, 6, 7]
        }

        payload = {
            "hash": self.api_key,
            "title": "Trip Report",
            "trackers": tracker_ids,
            "from": from_dt,
            "to": to_dt,
            "time_filter": time_filter,
            "plugin": plugin_params,
            "locale": "ru"
        }

        return self._post_with_retry(url, payload)

    def wait_for_report(self, report_id: int, timeout: int = 60):
        """
        Ожидание готовности отчета (поллинг).
        Возвращает итоговый статус или вызывает ошибку по таймауту.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self.get_report_status(report_id)
            if status and status.get("success") and status.get("percent_ready") == 100:
                return status
            time.sleep(3) # Чуть увеличим паузу между проверками
        raise TimeoutError(f"Report {report_id} generation timed out")
