#!/usr/bin/env python3
import os
import sys
import json
import logging
import threading
import time
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt
import google.generativeai as genai
from influxdb_client import InfluxDBClient

# Configure logging
LOG_FILE = "/data/logs/hermes.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("HermesAgent")

# Constants
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", None)
MQTT_PASS = os.getenv("MQTT_PASS", None)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# InfluxDB Constants
INFLUX_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN", "my-token")
INFLUX_ORG = os.getenv("INFLUXDB_ORG", "my-org")

INBOX_TOPIC = "solar/hermes/inbox"
OUTBOX_TOPIC = "solar/hermes/outbox"
STATUS_TOPIC = "solar/hermes/status"
GUARD_STATE_FILE = "/data/guard/guard_state.json"
EVA_REGISTRY_FILE = "/data/guard/eva_registry.json"

if not GOOGLE_API_KEY:
    logger.error("GOOGLE_API_KEY not set. Hermes will not be able to function.")

genai.configure(api_key=GOOGLE_API_KEY)

class HermesAgent:
    def __init__(self):
        self.mqtt_client = mqtt.Client(client_id="hermes_agent")
        if MQTT_USER and MQTT_PASS:
            self.mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        
        self.influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        
        self.model = None
        self.chat = None
        self.available_functions = {}
        self.setup_gemini()

    def setup_gemini(self):
        # 1. get_system_status
        def get_system_status():
            """Returns current system status including SOC, PV watts, and Energy Guard tier."""
            state = self.load_json_file(GUARD_STATE_FILE)
            return {
                "soc": state.get("current_soc"),
                "pv_watts": state.get("current_watts"),
                "guard_tier": state.get("current_tier"),
                "timestamp": state.get("timestamp")
            }

        # 2. get_solar_forecast
        def get_solar_forecast():
            """Returns a 3-day solar production forecast in kWh."""
            state = self.load_json_file(GUARD_STATE_FILE)
            forecast = state.get("forecast_daily_kwh", {})
            # Keep only next 3 days
            sorted_keys = sorted(forecast.keys())[:3]
            return {k: forecast[k] for k in sorted_keys}

        # 3. toggle_device
        def toggle_device(device_id: str, state: str):
            """Controls Home Assistant switches. device_id is the entity ID, state is 'ON' or 'OFF'."""
            topic = f"solar/switch/{device_id}/set"
            self.mqtt_client.publish(topic, state.upper(), retain=False)
            return f"Command sent: Set {device_id} to {state}"

        # 4. lock_device
        def lock_device(device_id: str, action: str):
            """Triggers EVA locking/unlocking for a device. action is 'LOCK' or 'UNLOCK'."""
            topic = f"solar/appliance/{device_id}/lock"
            self.mqtt_client.publish(topic, action.upper(), retain=True)
            return f"EVA {action} command sent for {device_id}"

        # 5. trigger_eva_map
        def trigger_eva_map():
            """Forces an immediate update and publication of the Energy Map."""
            self.mqtt_client.publish("solar/eva/command", "PUBLISH_MAP")
            return "Energy Map update triggered."

        # 6. trigger_eva_optimize
        def trigger_eva_optimize():
            """Starts the EVA schedule optimization process to find the best window for heavy loads."""
            self.mqtt_client.publish("solar/eva/command", "EVA_OPTIMIZE")
            return "EVA Optimization process started."

        # 7. trigger_eva_learn
        def trigger_eva_learn():
            """Starts the pattern learning engine to analyze historical device usage."""
            self.mqtt_client.publish("solar/eva/command", "EVA_LEARN")
            return "EVA Pattern Learning started."

        # 8. cut_phantom_loads
        def cut_phantom_loads():
            """Triggers an immediate check and cut of detected phantom loads."""
            self.mqtt_client.publish("solar/eva/command", "EVA_PHANTOM_CUT")
            return "Phantom load cut command sent."

        # 9. get_eva_registry
        def get_eva_registry():
            """Returns the current EVA device registry including priorities and classifications."""
            return self.load_json_file(EVA_REGISTRY_FILE)

        # 10. update_device_priority
        def update_device_priority(device_id: str, priority: str):
            """Updates the priority of a device in the EVA registry. priority can be 'PHANTOM', 'LUXURY', or 'SHIFTABLE'."""
            registry = self.load_json_file(EVA_REGISTRY_FILE)
            if device_id in registry:
                registry[device_id]["priority"] = priority.upper()
                self.save_json_file(EVA_REGISTRY_FILE, registry)
                # Notify Guard to reload
                self.mqtt_client.publish("solar/eva/command", "RELOAD_REGISTRY")
                return f"Updated {device_id} priority to {priority}."
            return f"Device {device_id} not found in registry."

        # 11. get_health_status
        def get_health_status():
            """Returns the latest system health metrics, including SSD, disk, and service status."""
            log_path = "/data/logs/health.log"
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r') as f:
                        lines = f.readlines()
                        if lines:
                            return {"last_check": lines[-1].strip()}
                except Exception as e:
                    logger.error(f"Error reading health log: {e}")
            return {"status": "Operational", "details": "All services running nominal."}

        # 12. get_energy_usage_summary
        def get_energy_usage_summary():
            """Returns a 24-hour energy usage summary from InfluxDB."""
            query = f'from(bucket: "system_state") |> range(start: -24h) |> filter(fn: (r) => r["_field"] == "load_watts") |> aggregateWindow(every: 1h, fn: mean, createEmpty: false) |> yield(name: "mean")'
            try:
                tables = self.influx_client.query_api().query(query)
                results = []
                for table in tables:
                    for record in table.records:
                        results.append({"time": record.get_time().isoformat(), "avg_watts": record.get_value()})
                return results
            except Exception as e:
                logger.error(f"Error querying InfluxDB: {e}")
                return {"error": "Could not retrieve usage summary."}

        self.available_functions = {
            "get_system_status": get_system_status,
            "get_solar_forecast": get_solar_forecast,
            "toggle_device": toggle_device,
            "lock_device": lock_device,
            "trigger_eva_map": trigger_eva_map,
            "trigger_eva_optimize": trigger_eva_optimize,
            "trigger_eva_learn": trigger_eva_learn,
            "cut_phantom_loads": cut_phantom_loads,
            "get_eva_registry": get_eva_registry,
            "update_device_priority": update_device_priority,
            "get_health_status": get_health_status,
            "get_energy_usage_summary": get_energy_usage_summary
        }

        self.model = genai.GenerativeModel(
            model_name='gemini-1.0-pro',
            tools=list(self.available_functions.values())
        )
        self.chat = self.model.start_chat()
        
        # System instruction for version 0.4.1 (sent as first message)
        try:
            self.chat.send_message("You are Hermes, the AI agent for Solar-Sentinel-AIO v3. You manage the energy system, EVA (Energy Value Analysis), and device control. Use tools to provide real-time data and execute commands. Be concise and professional.")
        except Exception as e:
            logger.error(f"Error sending system prompt: {e}")

    def load_json_file(self, filepath):
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading {filepath}: {e}")
        return {}

    def save_json_file(self, filepath, data):
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving {filepath}: {e}")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to MQTT Broker")
            client.subscribe(INBOX_TOPIC)
            client.publish(STATUS_TOPIC, "ONLINE", retain=True)
        else:
            logger.error(f"Failed to connect to MQTT, rc={rc}")

    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode()
            logger.info(f"Received message: {payload}")
            
            response = self.chat.send_message(payload)
            
            # Handle function calls manually for SDK 0.4.1
            while True:
                has_function_call = False
                if response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if part.function_call:
                            has_function_call = True
                            fn_name = part.function_call.name
                            fn_args = {k: v for k, v in part.function_call.args.items()}
                            logger.info(f"Calling function: {fn_name} with args {fn_args}")
                            
                            if fn_name in self.available_functions:
                                result = self.available_functions[fn_name](**fn_args)
                                logger.info(f"Function result: {result}")
                                response = self.chat.send_message(
                                    genai.protos.Content(
                                        parts=[genai.protos.Part(
                                            function_response=genai.protos.FunctionResponse(
                                                name=fn_name,
                                                response={'result': result}
                                            )
                                        )]
                                    )
                                )
                            else:
                                logger.error(f"Function {fn_name} not found")
                                response = self.chat.send_message(
                                    genai.protos.Content(
                                        parts=[genai.protos.Part(
                                            function_response=genai.protos.FunctionResponse(
                                                name=fn_name,
                                                response={'result': 'Error: Function not found'}
                                            )
                                        )]
                                    )
                                )
                            break
                if not has_function_call:
                    break
            
            try:
                final_text = response.text
            except Exception:
                final_text = "I have executed the requested command."

            out_msg = {
                "response": final_text,
                "timestamp": datetime.now().isoformat()
            }
            client.publish(OUTBOX_TOPIC, json.dumps(out_msg))
            logger.info(f"Sent response: {final_text}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            client.publish(OUTBOX_TOPIC, json.dumps({"error": str(e), "timestamp": datetime.now().isoformat()}))

    def run(self):
        while True:
            try:
                self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
                self.mqtt_client.loop_forever()
            except Exception as e:
                logger.error(f"MQTT connection error: {e}. Retrying in 5s...")
                time.sleep(5)

if __name__ == "__main__":
    agent = HermesAgent()
    agent.run()
