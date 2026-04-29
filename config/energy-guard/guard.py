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
import pvlib
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

# --- Section B: RotatingFileHandler logging ---
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

# --- Section C: JSON state persistence ---
STATE_FILE = "/data/guard/guard_state.json"
state = {
    "current_soc": 50.0,
    "current_watts": 0.0,
    "current_tier": "NOMINAL",
    "last_alert_tier": None,
    "config": {
        "soc_lockout": SOC_LOCKOUT,
        "soc_warning": SOC_WARNING,
        "soc_advisory": SOC_ADVISORY,
        "soc_abundance": SOC_ABUNDANCE
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

# --- Section D: MQTT Connection with retry loop ---
mqtt_client = mqtt.Client(client_id="solar_guard")
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT Broker")
        client.subscribe("home/battery/soc")
        client.subscribe("home/panels/total_w")
        client.subscribe("home/guard/config/#")
        client.subscribe("home/guard/command")
        client.publish("home/guard/status", "ONLINE", retain=True)
    else:
        logger.error(f"Failed to connect to MQTT, return code {rc}")

def on_message(client, userdata, msg):
    global state
    try:
        payload = msg.payload.decode()
        if msg.topic == "home/battery/soc":
            state["current_soc"] = float(payload)
        elif msg.topic == "home/panels/total_w":
            state["current_watts"] = float(payload)
        elif msg.topic.startswith("home/guard/config/"):
            key = msg.topic.split("/")[-1]
            state["config"][key] = float(payload)
            save_state()
            logger.info(f"Updated config {key} to {payload}")
        elif msg.topic == "home/guard/command":
            if payload == "FORCE_FORECAST":
                update_forecast()
            elif payload == "FORCE_DECISION":
                run_decision_engine()
    except Exception as e:
        logger.error(f"Error processing MQTT message on {msg.topic}: {e}")

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.will_set("home/guard/status", "OFFLINE", retain=True)

def mqtt_thread_func():
    while True:
        try:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            mqtt_client.loop_forever()
        except Exception as e:
            logger.error(f"MQTT Connection error: {e}. Retrying in 5s...")
            time.sleep(5)

# --- Section E: InfluxDB Connection with retry loop ---
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
            logger.error(f"InfluxDB connection failed: {e}. Retrying in 10s...")
            time.sleep(10)

# --- Section F: Weather + Solar Forecast Engine ---
DUBAI_CLOUD_COVER = {
    1: 25, 2: 28, 3: 31, 4: 28, 5: 15, 6: 10,
    7: 15, 8: 18, 9: 12, 10: 15, 11: 18, 12: 22
}

def get_weather_forecast():
    # Local Open-Meteo
    try:
        res = requests.get(f"http://localhost:8080/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&hourly=cloudcover,temperature_2m&timezone={TIMEZONE}", timeout=5)
        if res.status_code == 200:
            return res.json()
    except:
        pass

    # Public Open-Meteo
    try:
        res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&hourly=cloudcover,temperature_2m&timezone={TIMEZONE}", timeout=10)
        if res.status_code == 200:
            return res.json()
    except:
        pass

    # Statistical fallback
    logger.warning("Using statistical fallback for weather")
    now = datetime.now(pytz.timezone(TIMEZONE))
    hourly_times = []
    cloud_covers = []
    temps = []
    for i in range(168):
        dt = now + timedelta(hours=i)
        hourly_times.append(dt.strftime("%Y-%m-%dT%H:00"))
        cloud_covers.append(DUBAI_CLOUD_COVER[dt.month])
        temps.append(30.0)
    return {"hourly": {"time": hourly_times, "cloudcover": cloud_covers, "temperature_2m": temps}}

def update_forecast():
    logger.info("Updating Solar Forecast...")
    weather = get_weather_forecast()
    if not weather or 'hourly' not in weather:
        logger.error("Failed to get weather data")
        return

    try:
        times = pd.to_datetime(weather['hourly']['time'])
        cloudcover = weather['hourly']['cloudcover']
        temps = weather['hourly']['temperature_2m']
        
        loc = Location(LATITUDE, LONGITUDE, tz=TIMEZONE)
        clearsky = loc.get_clearsky(times)
        
        forecast_data = []
        total_yield_7day = 0
        
        for i in range(len(times)):
            ghi = clearsky['ghi'].iloc[i]
            # Simple cloud cover model
            cloud_factor = (1 - 0.75 * (cloudcover[i]/100.0)**3)
            actual_ghi = ghi * cloud_factor
            
            t_amb = temps[i]
            t_cell = t_amb + (actual_ghi / 800.0) * 20.0
            
            # HJT panel model: efficiency=0.215, temp_coefficient=-0.0026
            power = PANEL_AREA * actual_ghi * PANEL_EFFICIENCY * (1 + PANEL_TEMP_COEFF * (t_cell - 25))
            power = max(0, power)
            
            total_yield_7day += power # Watt-hours if hourly
            
            point = Point("solar_forecast") \
                .time(times[i], WritePrecision.NS) \
                .field("power_w", float(power)) \
                .field("cloudcover", float(cloudcover[i]))
            forecast_data.append(point)
            
        if write_api:
            write_api.write(bucket=INFLUXDB_BUCKET_FORECAST, record=forecast_data)
            logger.info(f"Written {len(forecast_data)} forecast points to InfluxDB")
        
        mqtt_client.publish("home/forecast/7day", round(total_yield_7day / 1000.0, 2))
    except Exception as e:
        logger.error(f"Error in forecast engine: {e}")

# --- Section G: Decision Engine ---
def run_decision_engine():
    global state
    soc = state["current_soc"]
    watts = state["current_watts"]
    cfg = state["config"]
    
    new_tier = "NOMINAL"
    
    if soc < cfg.get("soc_lockout", SOC_LOCKOUT):
        new_tier = "LOCKOUT"
    elif soc < cfg.get("soc_warning", SOC_WARNING):
        new_tier = "WARNING"
    elif soc < cfg.get("soc_advisory", SOC_ADVISORY):
        new_tier = "ADVISORY"
    elif soc > cfg.get("soc_abundance", SOC_ABUNDANCE) and watts > 2000:
        new_tier = "ABUNDANCE"
    else:
        new_tier = "NOMINAL"
        
    if new_tier != state["current_tier"]:
        logger.info(f"Tier transition: {state['current_tier']} -> {new_tier}")
        old_tier = state["current_tier"]
        state["current_tier"] = new_tier
        
        if new_tier == "LOCKOUT":
            lock_appliances()
            publish_alert("CRITICAL: Battery Critically Low. All heavy appliances locked.", priority="urgent", tags="skull")
        elif new_tier == "WARNING":
            publish_alert("WARNING: Battery Low. Please reduce consumption.", priority="high", tags="warning")
        elif new_tier == "ADVISORY":
            publish_alert("ADVISORY: Energy conservation recommended.", priority="default", tags="info")
        elif new_tier == "NOMINAL":
            if old_tier == "LOCKOUT" or old_tier == "WARNING":
                unlock_all_appliances()
            publish_alert("NOMINAL: Energy levels normal.", priority="low", tags="white_check_mark")
        elif new_tier == "ABUNDANCE":
            unlock_all_appliances()
            publish_alert("ABUNDANCE: Excess solar detected! Feel free to use heavy appliances.", priority="low", tags="sunny")
            
    save_state()

# --- Section H: lock_appliances() and unlock_all_appliances() ---
APPLIANCES = ["washing_machine", "dishwasher", "water_heater", "ac_unit", "ev_charger"]

def lock_appliances():
    for app in APPLIANCES:
        mqtt_client.publish(f"home/appliances/{app}/lock", "LOCK", qos=1, retain=True)
    logger.info("Sent LOCK command to all appliances")

def unlock_all_appliances():
    for app in APPLIANCES:
        mqtt_client.publish(f"home/appliances/{app}/lock", "UNLOCK", qos=1, retain=True)
    logger.info("Sent UNLOCK command to all appliances")

# --- Section I: publish_alert() ---
def publish_alert(message, priority="default", tags=""):
    # ntfy.sh
    try:
        requests.post(NTFY_URL, 
            data=message.encode('utf-8'),
            headers={
                "Title": "Solar Sentinel Guard",
                "Priority": priority,
                "Tags": tags
            },
            timeout=5
        )
    except Exception as e:
        logger.error(f"Failed to send ntfy alert: {e}")
    
    # MQTT
    mqtt_client.publish("home/alerts/energy_guard", json.dumps({
        "message": message,
        "priority": priority,
        "tags": tags,
        "timestamp": datetime.now().isoformat()
    }))

# --- Section J: write_state_snapshot() ---
def write_state_snapshot():
    if not write_api:
        return
    try:
        point = Point("system_state") \
            .field("soc", float(state["current_soc"])) \
            .field("watts", float(state["current_watts"])) \
            .tag("tier", state["current_tier"])
        write_api.write(bucket=INFLUXDB_BUCKET_STATE, record=point)
    except Exception as e:
        logger.error(f"Error writing snapshot to InfluxDB: {e}")

# --- Section K: schedule setup and main loop ---
def signal_handler(sig, frame):
    logger.info(f"Signal {sig} received. Shutting down...")
    save_state()
    try:
        mqtt_client.publish("home/guard/status", "OFFLINE", retain=True)
        mqtt_client.disconnect()
    except:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to config file")
    args, unknown = parser.parse_known_args()
    
    if args.config and os.path.exists(args.config):
        try:
            with open(args.config, 'r') as f:
                config_data = json.load(f)
                # Apply config from file if not overridden by ENV
                # (For simplicity, we'll just log it here)
                logger.info(f"Loaded config from {args.config}")
        except Exception as e:
            logger.error(f"Error reading config file: {e}")

    logger.info("Starting Energy Guard Brain...")
    load_state()
    
    # Start InfluxDB init in a separate thread to not block MQTT
    threading.Thread(target=init_influx, daemon=True).start()
    
    # Start MQTT loop in thread
    threading.Thread(target=mqtt_thread_func, daemon=True).start()
    
    # Wait for connections
    time.sleep(5)
    
    # Initial runs
    update_forecast()
    run_decision_engine()
    
    # Schedule setup
    schedule.every(5).minutes.do(write_state_snapshot)
    schedule.every(15).minutes.do(run_decision_engine)
    schedule.every(6).hours.do(update_forecast)
    schedule.every().day.at("05:30").do(update_forecast)
    
    logger.info("Schedules initialized")
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
