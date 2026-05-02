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

INBOX_TOPIC = "solar/hermes/inbox"
OUTBOX_TOPIC = "solar/hermes/outbox"
STATUS_TOPIC = "solar/hermes/status"
GUARD_STATE_FILE = "/data/guard/guard_state.json"

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
        
        self.model = None
        self.chat = None
        self.available_functions = {}
        self.setup_gemini()

    def setup_gemini(self):
        # Define the tools
        def get_system_status():
            """Returns SOC, Watts, and Current Tier."""
            state = self.load_guard_state()
            return {
                "soc": state.get("current_soc"),
                "watts": state.get("current_watts"),
                "tier": state.get("current_tier")
            }

        def get_solar_forecast():
            """Returns 7-day yield predictions."""
            state = self.load_guard_state()
            return state.get("forecast_daily_kwh", {})

        def get_eva_map():
            """Retrieves the current Energy Map."""
            state = self.load_guard_state()
            return state.get("eva", {}).get("nodes", {})

        def get_optimal_window():
            """Returns the best 4-hour window for heavy loads."""
            state = self.load_guard_state()
            return state.get("eva", {}).get("last_optimal_window")

        def control_appliance(device_id: str, action: str):
            """Locks/Unlocks devices. Action must be 'LOCK' or 'UNLOCK'."""
            topic = f"solar/appliance/{device_id}/lock"
            self.mqtt_client.publish(topic, action.upper(), retain=True)
            return f"Action {action} sent to {device_id}"

        def set_thresholds(lockout: float = None, warning: float = None, advisory: float = None, abundance: float = None):
            """Updates Guard SOC thresholds."""
            if lockout is not None: self.mqtt_client.publish("solar/guard/config/soc_lockout", str(lockout), retain=True)
            if warning is not None: self.mqtt_client.publish("solar/guard/config/soc_warning", str(warning), retain=True)
            if advisory is not None: self.mqtt_client.publish("solar/guard/config/soc_advisory", str(advisory), retain=True)
            if abundance is not None: self.mqtt_client.publish("solar/guard/config/soc_abundance", str(abundance), retain=True)
            return "Threshold update commands sent."

        def get_thresholds():
            """Reads current Guard configuration."""
            state = self.load_guard_state()
            return state.get("config", {})

        def trigger_phantom_cut():
            """Executes immediate waste analysis and cut."""
            self.mqtt_client.publish("solar/eva/command", "EVA_PHANTOM_CUT")
            return "Phantom cut command triggered."

        def trigger_optimization():
            """Runs the Optimal Window Finder."""
            self.mqtt_client.publish("solar/eva/command", "EVA_OPTIMIZE")
            return "Optimization command triggered."

        def trigger_learning():
            """Starts pattern recognition on historical data."""
            self.mqtt_client.publish("solar/eva/command", "EVA_LEARN")
            return "Pattern learning command triggered."

        def get_alerts():
            """Fetches recent system security/energy alerts."""
            state = self.load_guard_state()
            return {"last_alert_tier": state.get("last_alert_tier")}

        def get_device_intelligence(node_id: str):
            """Returns learned behavior patterns for a specific node."""
            state = self.load_guard_state()
            patterns = state.get("eva", {}).get("patterns", {})
            return patterns.get(node_id, "No patterns found for this node.")

        self.available_functions = {
            "get_system_status": get_system_status,
            "get_solar_forecast": get_solar_forecast,
            "get_eva_map": get_eva_map,
            "get_optimal_window": get_optimal_window,
            "control_appliance": control_appliance,
            "set_thresholds": set_thresholds,
            "get_thresholds": get_thresholds,
            "trigger_phantom_cut": trigger_phantom_cut,
            "trigger_optimization": trigger_optimization,
            "trigger_learning": trigger_learning,
            "get_alerts": get_alerts,
            "get_device_intelligence": get_device_intelligence
        }

        self.model = genai.GenerativeModel(
            model_name='gemini-1.0-pro',
            tools=list(self.available_functions.values())
        )
        self.chat = self.model.start_chat()
        
        try:
            self.chat.send_message("You are Hermes, the AI agent for Solar-Sentinel-AIO. You have access to system tools to manage energy, EVA (Energy Visual Analytics), and appliance control. Use the provided tools to help the user.")
        except Exception as e:
            logger.error(f"Error sending system prompt: {e}")

    def load_guard_state(self):
        if os.path.exists(GUARD_STATE_FILE):
            try:
                with open(GUARD_STATE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading guard state: {e}")
        return {}

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
            
            # Handle function calls
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
