#!/usr/bin/env python3
import os
import sys
import json
import logging
import signal
import threading
import queue
from datetime import datetime

import paho.mqtt.client as mqtt
from langchain_community.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain.memory import ConversationBufferMemory

# Configure logging
LOG_FILE = "/data/logs/hermes.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("HermesAgent")

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", None)
MQTT_PASS = os.getenv("MQTT_PASS", None)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "hermes-3-llama3.1:8b-q4_K_M")

INBOX_TOPIC = "solar/hermes/inbox"
OUTBOX_TOPIC = "solar/hermes/outbox"
STATUS_TOPIC = "solar/hermes/status"

message_queue = queue.Queue(maxsize=100)

class HermesAgent:
    def __init__(self):
        self.mqtt_client = None
        self.chain = None
        self.memory = ConversationBufferMemory(memory_key="chat_history")
        self.running = True

    def init_llm(self):
        llm = Ollama(base_url=OLLAMA_BASE_URL, model=OLLAMA_MODEL, temperature=0.7)
        system_prompt = """You are Hermes, the AI assistant for Solar Sentinel Phase 5.
Knowledge:
- EVA (Energy Visual Analytics): Energy Map, Phantom Load Detection, Pattern Learning.
- 5 Tiers: LOCKOUT, WARNING, ADVISORY, NOMINAL, ABUNDANCE.
Tools 9-12:
- Energy Map: Trigger via EVA_PUBLISH_MAP
- Reschedule: Trigger via EVA_OPTIMIZE
- Classify: Trigger via EVA_LEARN
- Waste Analysis: Trigger via EVA_PHANTOM_CUT

Be helpful and concise."""
        prompt = PromptTemplate(input_variables=["input", "chat_history"], template=f"{system_prompt}\n\nChat:\n{{chat_history}}\nUser: {{input}}\nHermes:")
        self.chain = LLMChain(llm=llm, prompt=prompt, memory=self.memory)

    def init_mqtt(self):
        self.mqtt_client = mqtt.Client(client_id="hermes_agent")
        if MQTT_USER and MQTT_PASS: self.mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.mqtt_client.on_connect = lambda c, u, f, rc: (c.subscribe(INBOX_TOPIC), c.publish(STATUS_TOPIC, "ONLINE", retain=True))
        self.mqtt_client.on_message = lambda c, u, m: message_queue.put({"payload": m.payload.decode()})
        self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        threading.Thread(target=self.mqtt_client.loop_forever, daemon=True).start()

    def process_loop(self):
        while self.running:
            msg = message_queue.get()
            user_input = msg["payload"].lower()
            
            if "map" in user_input: self.mqtt_client.publish("solar/eva/command", "EVA_PUBLISH_MAP")
            if "optimize" in user_input or "reschedule" in user_input: self.mqtt_client.publish("solar/eva/command", "EVA_OPTIMIZE")
            if "learn" in user_input or "classify" in user_input: self.mqtt_client.publish("solar/eva/command", "EVA_LEARN")
            if "phantom" in user_input or "waste" in user_input: self.mqtt_client.publish("solar/eva/command", "EVA_PHANTOM_CUT")
            
            response = self.chain.run(input=msg["payload"])
            self.mqtt_client.publish(OUTBOX_TOPIC, json.dumps({"response": response, "timestamp": datetime.now().isoformat()}))

    def run(self):
        self.init_mqtt()
        self.init_llm()
        self.process_loop()

if __name__ == "__main__":
    agent = HermesAgent()
    agent.run()
