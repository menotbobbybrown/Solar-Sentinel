import os
import sys
import time
import json
import logging
import signal
import threading
from datetime import datetime, timedelta
from pathlib import Path
import math

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import requests
import schedule
import pytz
import pandas as pd
import numpy as np
from pvlib.location import Location
from logging.handlers import RotatingFileHandler

# --- Section A: Environment variable configuration ---
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", None)
MQTT_PASS = os.getenv("MQTT_PASS", None)

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "my-token")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET_FORECAST = os.getenv("INFLUXDB_BUCKET_FORECAST", "solar_forecast")
INFLUXDB_BUCKET_STATE = os.getenv("INFLUXDB_BUCKET_STATE", "system_state")
INFLUXDB_BUCKET_EVA_NODES = os.getenv("INFLUXDB_BUCKET_EVA_NODES", "eva_nodes")
INFLUXDB_BUCKET_EVA_PATTERNS = os.getenv("INFLUXDB_BUCKET_EVA_PATTERNS", "eva_patterns")

LATITUDE = float(os.getenv("LATITUDE", 25.2048))
LONGITUDE = float(os.getenv("LONGITUDE", 55.2708))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Dubai")

PANEL_EFFICIENCY = float(os.getenv("PANEL_EFFICIENCY", 0.215))
PANEL_TEMP_COEFF = float(os.getenv("PANEL_TEMP_COEFF", -0.0026))
PANEL_AREA = float(os.getenv("PANEL_AREA", 20.0))

NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh/solar_sentinel_guard_alerts")

# Thresholds
SOC_LOCKOUT = float(os.getenv("SOC_LOCKOUT", 20.0))
SOC_WARNING = float(os.getenv("SOC_WARNING", 40.0))
SOC_ADVISORY = float(os.getenv("SOC_ADVISORY", 60.0))
SOC_ABUNDANCE = float(os.getenv("SOC_ABUNDANCE", 90.0))

SOC_HYSTERESIS = 2.0
POWER_HYSTERESIS = 100

# --- Section B: Logging ---
LOG_FILE = "/data/logs/guard.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logger = logging.getLogger("EnergyGuard")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)
logger.addHandler(stdout_handler)

# --- Section C: State Persistence ---
STATE_FILE = "/data/guard/guard_state.json"
REGISTRY_FILE = "/data/guard/eva_registry.json"

state = {
    "current_soc": 50.0,
    "current_watts": 0.0,
    "current_tier": "NOMINAL",
    "last_alert_tier": None,
    "previous_tier": None,
    "forecast_3h_avg": 0.0,
    "forecast_daily_kwh": {},
    "config": {
        "soc_lockout": SOC_LOCKOUT,
        "soc_warning": SOC_WARNING,
        "soc_advisory": SOC_ADVISORY,
        "soc_abundance": SOC_ABUNDANCE
    },
    "eva": {
        "nodes": {},
        "patterns": {},
        "recommendations": [],
        "last_optimal_window": None,
        "phantom_cuts_performed": 0
    }
}

def load_state():
    global state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                loaded = json.load(f)
                if "config" in loaded:
                    state["config"].update(loaded["config"])
                for k, v in loaded.items():
                    if k != "config":
                        state[k] = v
        except Exception as e:
            logger.error(f"Error loading state: {e}")

def save_state():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

# --- Section D: MQTT Setup ---
MQTT_TOPICS = {
    "battery_soc": "solar/battery/soc",
    "pv_power": "solar/pv/power",
    "guard_status": "solar/guard/status",
    "guard_config": "solar/guard/config/",
    "guard_command": "solar/guard/command",
    "forecast_7day": "solar/forecast/7day",
    "alerts_guard": "solar/alerts/guard",
    "hermes_inbox": "solar/hermes/inbox",
    "hermes_outbox": "solar/hermes/outbox",
    "eva_map": "solar/eva/map",
    "eva_nodes": "solar/eva/node/#",
    "eva_command": "solar/eva/command",
}

mqtt_client = mqtt.Client(client_id="solar_guard")
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT Broker")
        client.subscribe(MQTT_TOPICS["battery_soc"])
        client.subscribe(MQTT_TOPICS["pv_power"])
        client.subscribe(MQTT_TOPICS["guard_config"] + "#")
        client.subscribe(MQTT_TOPICS["guard_command"])
        client.subscribe(MQTT_TOPICS["eva_nodes"])
        client.subscribe(MQTT_TOPICS["eva_command"])
        client.publish(MQTT_TOPICS["guard_status"], "ONLINE", retain=True)
    else:
        logger.error(f"Failed to connect to MQTT, rc={rc}")

def on_message(client, userdata, msg):
    global state
    try:
        payload = msg.payload.decode()
        if msg.topic == MQTT_TOPICS["battery_soc"]:
            state["current_soc"] = float(payload)
        elif msg.topic == MQTT_TOPICS["pv_power"]:
            state["current_watts"] = float(payload)
        elif msg.topic.startswith(MQTT_TOPICS["guard_config"]):
            key = msg.topic.split("/")[-1]
            state["config"][key] = float(payload)
            save_state()
        elif msg.topic == MQTT_TOPICS["guard_command"]:
            if payload == "FORCE_FORECAST": update_forecast()
            elif payload == "FORCE_DECISION": run_decision_engine()
        elif msg.topic.startswith("solar/eva/node/"):
            parts = msg.topic.split("/")
            if len(parts) >= 5:
                handle_eva_node_update(parts[3], parts[4], payload)
        elif msg.topic == MQTT_TOPICS["eva_command"]:
            handle_eva_command(payload)
    except Exception as e:
        logger.error(f"MQTT msg error: {e}")

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.will_set(MQTT_TOPICS["guard_status"], "OFFLINE", retain=True)

def mqtt_thread_func():
    while True:
        try:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            mqtt_client.loop_forever()
        except Exception as e:
            logger.error(f"MQTT Error: {e}. Retrying...")
            time.sleep(5)

# --- Section E: InfluxDB ---
influx_client = None
write_api = None

def init_influx():
    global influx_client, write_api
    while influx_client is None:
        try:
            influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
            write_api = influx_client.write_api(write_options=SYNCHRONOUS)
            logger.info("Connected to InfluxDB")
        except Exception as e:
            logger.error(f"InfluxDB init failed: {e}. Retrying...")
            time.sleep(10)

# --- Section F: Forecast Engine ---
def get_weather_forecast():
    for url in [f"http://localhost:8080/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&hourly=cloudcover,temperature_2m&timezone={TIMEZONE}",
                f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&hourly=cloudcover,temperature_2m&timezone={TIMEZONE}"]:
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200: return res.json()
        except: continue
    return None

def update_forecast():
    logger.info("Updating Solar Forecast...")
    weather = get_weather_forecast()
    if not weather: return

    try:
        times = pd.to_datetime(weather['hourly']['time'])
        cloudcover = weather['hourly']['cloudcover']
        loc = Location(LATITUDE, LONGITUDE, tz=TIMEZONE)
        clearsky = loc.get_clearsky(times)
        
        forecast_data = []
        daily_yields = {}
        for i in range(len(times)):
            power = PANEL_AREA * clearsky['ghi'].iloc[i] * PANEL_EFFICIENCY * (1 - 0.75 * (cloudcover[i]/100.0)**3)
            power = max(0, power)
            day_str = times[i].strftime("%Y-%m-%d")
            daily_yields[day_str] = daily_yields.get(day_str, 0) + (power / 1000.0)
            forecast_data.append(Point("solar_forecast").time(times[i], WritePrecision.NS).field("power_w", float(power)))
            
        if write_api: write_api.write(bucket=INFLUXDB_BUCKET_FORECAST, record=forecast_data)
        state["forecast_daily_kwh"] = daily_yields
        mqtt_client.publish(MQTT_TOPICS["forecast_7day"], round(sum(daily_yields.values()), 2))
        logger.info(f"Forecast updated. Next 3 days: {[daily_yields.get((datetime.now(pytz.timezone(TIMEZONE)) + timedelta(days=i)).strftime('%Y-%m-%d'), 0) for i in range(3)]}")
    except Exception as e:
        logger.error(f"Forecast engine error: {e}")

# --- Section G: Decision Engine ---
def run_decision_engine():
    global state
    soc = state["current_soc"]
    cfg = state["config"]
    current_tier = state["current_tier"]
    
    # Calculate worst_3day
    now_tz = datetime.now(pytz.timezone(TIMEZONE))
    forecast_daily = state.get("forecast_daily_kwh", {})
    next_3_days = [forecast_daily.get((now_tz + timedelta(days=i)).strftime("%Y-%m-%d"), 0.0) for i in range(3)]
    worst_3day = min(next_3_days) if next_3_days else 0.0
    
    new_tier = "NOMINAL"
    if worst_3day < 0.8: new_tier = "LOCKOUT"
    elif worst_3day < 2.5: new_tier = "WARNING"
    elif soc < cfg["soc_lockout"]: new_tier = "LOCKOUT"
    elif soc < cfg["soc_warning"]: new_tier = "WARNING"
    elif soc < cfg["soc_advisory"]: new_tier = "ADVISORY"
    elif soc > cfg["soc_abundance"] and state["current_watts"] > 2000: new_tier = "ABUNDANCE"
    
    if new_tier != current_tier:
        state["previous_tier"] = current_tier
        state["current_tier"] = new_tier
        logger.info(f"Tier transition: {current_tier} -> {new_tier}")
        if new_tier == "LOCKOUT": lock_appliances()
        elif current_tier in ["LOCKOUT", "WARNING"] and new_tier == "NOMINAL": unlock_all_appliances()
        publish_alert(f"Tier changed to {new_tier}. SOC: {soc}%, Forecast Worst: {worst_3day:.2f}kWh", old_tier=current_tier, new_tier=new_tier)
    save_state()

# --- EVA SECTIONS (A-H) ---

# Section A: Registry Management
def load_eva_registry():
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading EVA registry: {e}")
    return []

# Section B: Phantom Cut Logic
def eva_phantom_cut():
    registry = load_eva_registry()
    nodes = state["eva"]["nodes"]
    cuts = 0
    for item in registry:
        if item.get("priority") == "PHANTOM":
            node = nodes.get(item["id"], {})
            if node.get("last_power", 0) > 5.0 and node.get("last_state") in ["OFF", "STANDBY"]:
                mqtt_client.publish(f"solar/eva/node/{item['id']}/cut", "CUT")
                cuts += 1
    if cuts > 0:
        state["eva"]["phantom_cuts_performed"] += cuts
        publish_alert(f"EVA: Cut {cuts} phantom loads.", priority="default", tags="zap")

# Section C: Optimal Window Finder
def eva_optimal_window_finder():
    logger.info("EVA: Finding optimal 4-hour window...")
    if not influx_client: return
    
    query_api = influx_client.query_api()
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET_FORECAST}")
      |> range(start: now(), stop: 48h)
      |> filter(fn: (r) => r["_measurement"] == "solar_forecast")
      |> filter(fn: (r) => r["_field"] == "power_w")
      |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
    '''
    try:
        tables = query_api.query(query)
        forecast_data = []
        for table in tables:
            for record in table.records:
                forecast_data.append({"time": record.get_time(), "power": record.get_value()})
        
        if len(forecast_data) < 4:
            logger.warning("Not enough forecast data for optimal window finder.")
            return

        max_yield = 0
        best_start_time = None
        
        for i in range(len(forecast_data) - 3):
            current_yield = sum(forecast_data[j]["power"] for j in range(i, i+4))
            if current_yield > max_yield:
                max_yield = current_yield
                best_start_time = forecast_data[i]["time"]
        
        if best_start_time:
            window = {
                "start_time": best_start_time.isoformat() if hasattr(best_start_time, 'isoformat') else str(best_start_time),
                "end_time": ((best_start_time + timedelta(hours=4)).isoformat()) if hasattr(best_start_time, 'isoformat') else str(best_start_time),
                "predicted_yield_wh": round(max_yield, 2)
            }
            state["eva"]["last_optimal_window"] = window
            mqtt_client.publish("solar/eva/optimal_window", json.dumps(window), retain=True)
            logger.info(f"Optimal window found: {best_start_time}")
            save_state()
    except Exception as e:
        logger.error(f"Error in optimal window finder: {e}")

# Section D: Smart Lockout (EVA-Aware)
def lock_appliances():
    registry = load_eva_registry()
    tier = state["current_tier"]
    for item in registry:
        should_lock = False
        if tier == "LOCKOUT" and item["priority"] in ["PHANTOM", "LUXURY", "SHIFTABLE"]: should_lock = True
        elif tier == "WARNING" and item["priority"] in ["PHANTOM", "LUXURY"]: should_lock = True
        
        if should_lock:
            mqtt_client.publish(f"solar/appliance/{item['id']}/lock", "LOCK", retain=True)
            logger.info(f"EVA Lock: {item['id']}")

def unlock_all_appliances():
    registry = load_eva_registry()
    for item in registry:
        mqtt_client.publish(f"solar/appliance/{item['id']}/lock", "UNLOCK", retain=True)
    logger.info("EVA Unlock: All appliances")

# Section E: Pattern Learning
def eva_pattern_learning():
    logger.info("EVA: Learning energy patterns from InfluxDB...")
    if not influx_client: return
    
    query_api = influx_client.query_api()
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET_EVA_NODES}")
      |> range(start: -7d)
      |> filter(fn: (r) => r["_measurement"] == "eva_nodes")
      |> filter(fn: (r) => r["_field"] == "power_w")
      |> group(columns: ["node_id"])
      |> mean()
    '''
    try:
        tables = query_api.query(query)
        for table in tables:
            for record in table.records:
                node_id = record.values.get("node_id")
                mean_power = record.get_value()
                state["eva"]["patterns"][node_id] = {
                    "avg_w": round(mean_power, 2),
                    "confidence": 0.85,
                    "last_learned": datetime.now().isoformat()
                }
                point = Point("eva_patterns").tag("node_id", node_id).field("avg_w", float(mean_power))
                write_api.write(bucket=INFLUXDB_BUCKET_EVA_PATTERNS, record=point)
        logger.info("EVA: Pattern learning complete.")
        save_state()
    except Exception as e:
        logger.error(f"Error in pattern learning: {e}")

# Section F: Map Snapshot
def eva_publish_map():
    energy_map = {
        "timestamp": datetime.now().isoformat(),
        "system": {"soc": state["current_soc"], "tier": state["current_tier"]},
        "nodes": state["eva"]["nodes"],
        "phantom_total": state["eva"]["phantom_cuts_performed"]
    }
    mqtt_client.publish(MQTT_TOPICS["eva_map"], json.dumps(energy_map), retain=True)

# Section G: Helpers
def handle_eva_node_update(node_id, data_type, payload):
    if node_id not in state["eva"]["nodes"]: state["eva"]["nodes"][node_id] = {}
    node = state["eva"]["nodes"][node_id]
    if data_type == "power": node["last_power"] = float(payload)
    elif data_type == "state": node["last_state"] = payload
    
    if write_api and data_type == "power":
        point = Point("eva_nodes").tag("node_id", node_id).field("power_w", float(payload))
        write_api.write(bucket=INFLUXDB_BUCKET_EVA_NODES, record=point)

def handle_eva_command(payload):
    if payload == "EVA_PHANTOM_CUT": eva_phantom_cut()
    elif payload == "EVA_PUBLISH_MAP": eva_publish_map()
    elif payload == "EVA_OPTIMIZE": eva_optimal_window_finder()
    elif payload == "EVA_LEARN": eva_pattern_learning()

# Section H: Schedule
def setup_eva_schedule():
    schedule.every(1).minutes.do(eva_publish_map)
    schedule.every(1).hours.do(eva_phantom_cut)
    schedule.every(6).hours.do(eva_optimal_window_finder)
    schedule.every().day.at("02:00").do(eva_pattern_learning)

# --- Final Boilerplate ---
def publish_alert(msg, old_tier=None, new_tier=None, priority="default", tags=""):
    alert = {"message": msg, "timestamp": datetime.now().isoformat(), "soc": state["current_soc"]}
    mqtt_client.publish(MQTT_TOPICS["alerts_guard"], json.dumps(alert))
    try: requests.post(NTFY_URL, data=msg, headers={"Title": "Solar Guard", "Priority": priority, "Tags": tags}, timeout=5)
    except: pass

def main():
    load_state()
    threading.Thread(target=init_influx, daemon=True).start()
    threading.Thread(target=mqtt_thread_func, daemon=True).start()
    time.sleep(5)
    update_forecast()
    run_decision_engine()
    setup_eva_schedule()
    schedule.every(5).minutes.do(run_decision_engine)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
