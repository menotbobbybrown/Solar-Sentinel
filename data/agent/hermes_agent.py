#!/usr/bin/env python3
"""
Hermes Agent - Solar Sentinel AI Assistant
LangChain-based agent for natural language control of the solar system.
Listens on MQTT solar/hermes/inbox and responds on solar/hermes/outbox.
"""

import os
import sys
import json
import logging
import signal
import threading
import queue
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from langchain.llms import Ollama
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain.agents import AgentExecutor, create_json_agent
from langchain.tools import Tool
from langchain.memory import ConversationBufferMemory

# Configure logging
LOG_FILE = "/data/logs/hermes.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("HermesAgent")

# MQTT Configuration
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", None)
MQTT_PASS = os.getenv("MQTT_PASS", None)

# Ollama Configuration
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "hermes-3-llama3.1:8b-q4_K_M")

# Topics
INBOX_TOPIC = "solar/hermes/inbox"
OUTBOX_TOPIC = "solar/hermes/outbox"
STATUS_TOPIC = "solar/hermes/status"

# State file
HISTORY_FILE = "/data/agent/hermes_history.json"
MAX_HISTORY = 50

# Message queue for async processing
message_queue = queue.Queue(maxsize=100)


class HermesAgent:
    def __init__(self):
        self.mqtt_client = None
        self.llm = None
        self.chain = None
        self.memory = ConversationBufferMemory(memory_key="chat_history", input_key="input", output_key="response")
        self.running = True
        self.load_history()
        
    def load_history(self):
        """Load conversation history from file."""
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    history = json.load(f)
                    for entry in history:
                        self.memory.save_context(
                            {"input": entry.get("input", "")},
                            {"response": entry.get("response", "")}
                        )
                logger.info(f"Loaded {len(history)} history entries")
            except Exception as e:
                logger.error(f"Error loading history: {e}")
    
    def save_history(self):
        """Save conversation history to file."""
        try:
            history = self.memory.load_memory_variables({}).get("chat_history", [])
            with open(HISTORY_FILE, 'w') as f:
                json.dump(history[-MAX_HISTORY:], f, indent=2)
        except Exception as e:
            logger.error(f"Error saving history: {e}")
    
    def init_llm(self):
        """Initialize the Ollama LLM and chain."""
        try:
            logger.info(f"Initializing LLM with model: {OLLAMA_MODEL}")
            self.llm = Ollama(
                base_url=OLLAMA_BASE_URL,
                model=OLLAMA_MODEL,
                temperature=0.7,
                top_p=0.9,
                repeat_penalty=1.1
            )
            
            # Create a system prompt for the solar assistant
            system_prompt = """You are Hermes, the AI assistant for Solar Sentinel, an advanced energy management system.
            
You help users understand and control their solar energy system, including:
- Battery state-of-charge monitoring and alerts
- Solar panel power production
- Appliance locking during low battery conditions
- Energy forecasting based on weather data
- System health and monitoring

The system uses a 5-tier alert system:
- LOCKOUT: Battery critically low (<20%), all heavy appliances locked
- WARNING: Battery low (<40%), conservation recommended
- ADVISORY: Battery moderate (<60%), be mindful of usage
- NOMINAL: Battery healthy, normal operation
- ABUNDANCE: Battery high (>90%) with excess solar (>2000W), free to use power

Be helpful, concise, and informative. Provide actionable advice when possible.
When users ask about system status, be specific with values when available."""
            
            prompt = PromptTemplate(
                input_variables=["input", "chat_history"],
                template=f"""{system_prompt}

Chat History:
{{chat_history}}

Human: {{input}}
Hermes:"""
            )
            
            self.chain = LLMChain(llm=self.llm, prompt=prompt, memory=self.memory)
            logger.info("LLM chain initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Error initializing LLM: {e}")
            return False
    
    def init_mqtt(self):
        """Initialize MQTT connection."""
        self.mqtt_client = mqtt.Client(client_id="hermes_agent")
        if MQTT_USER and MQTT_PASS:
            self.mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        
        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                logger.info("Connected to MQTT Broker")
                client.subscribe(INBOX_TOPIC)
                client.publish(STATUS_TOPIC, "ONLINE", retain=True)
            else:
                logger.error(f"Failed to connect to MQTT, return code {rc}")
        
        def on_disconnect(client, userdata, rc):
            logger.warning(f"Disconnected from MQTT with code {rc}")
        
        def on_message(client, userdata, msg):
            try:
                payload = msg.payload.decode()
                logger.info(f"Received message on {msg.topic}: {payload[:100]}...")
                message_queue.put({
                    "topic": msg.topic,
                    "payload": payload,
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                logger.error(f"Error processing MQTT message: {e}")
        
        self.mqtt_client.on_connect = on_connect
        self.mqtt_client.on_disconnect = on_disconnect
        self.mqtt_client.on_message = on_message
        self.mqtt_client.will_set(STATUS_TOPIC, "OFFLINE", retain=True)
        
        return self._connect_mqtt()
    
    def _connect_mqtt(self):
        """Connect to MQTT with retry logic."""
        retry_count = 0
        max_retries = 10
        
        while retry_count < max_retries:
            try:
                self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
                logger.info("MQTT connection established")
                return True
            except Exception as e:
                retry_count += 1
                wait_time = min(30, 2 ** retry_count)
                logger.error(f"MQTT connection failed (attempt {retry_count}/{max_retries}): {e}. Retrying in {wait_time}s...")
                threading.Event().wait(wait_time)
        
        logger.error("Failed to connect to MQTT after maximum retries")
        return False
    
    def process_message(self, message):
        """Process an incoming message and generate a response."""
        try:
            user_input = message.get("payload", "")
            
            if not user_input.strip():
                return
            
            logger.info(f"Processing message: {user_input[:100]}...")
            
            # Check if LLM is initialized
            if not self.chain:
                response = "I apologize, but I'm not fully initialized yet. Please try again in a moment."
            else:
                try:
                    # Generate response
                    response = self.chain.run(input=user_input)
                    logger.info(f"Generated response: {response[:100]}...")
                except Exception as e:
                    logger.error(f"Error generating response: {e}")
                    response = "I encountered an error processing your request. Please try again."
            
            # Publish response to outbox
            response_data = {
                "response": response,
                "timestamp": datetime.now().isoformat(),
                "original_input": user_input
            }
            
            self.mqtt_client.publish(OUTBOX_TOPIC, json.dumps(response_data))
            logger.info(f"Published response to {OUTBOX_TOPIC}")
            
            # Save history
            self.save_history()
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def mqtt_loop(self):
        """Run the MQTT loop in a thread."""
        try:
            self.mqtt_client.loop_forever()
        except Exception as e:
            logger.error(f"MQTT loop error: {e}")
    
    def message_processor(self):
        """Process messages from the queue."""
        while self.running:
            try:
                message = message_queue.get(timeout=1)
                self.process_message(message)
                message_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in message processor: {e}")
    
    def signal_handler(self, sig, frame):
        """Handle shutdown signals."""
        logger.info(f"Signal {sig} received. Shutting down...")
        self.running = False
        
        try:
            self.mqtt_client.publish(STATUS_TOPIC, "OFFLINE", retain=True)
            self.mqtt_client.disconnect()
        except:
            pass
        
        self.save_history()
        sys.exit(0)
    
    def run(self):
        """Main run loop."""
        logger.info("Starting Hermes Agent...")
        
        # Register signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Initialize MQTT
        if not self.init_mqtt():
            logger.error("Failed to initialize MQTT. Exiting.")
            sys.exit(1)
        
        # Initialize LLM with retry
        llm_ready = False
        for attempt in range(5):
            if self.init_llm():
                llm_ready = True
                break
            logger.warning(f"LLM initialization failed (attempt {attempt + 1}/5). Retrying...")
            threading.Event().wait(5)
        
        if not llm_ready:
            logger.warning("LLM not available. Hermes will respond with initialization message.")
        
        # Start MQTT loop thread
        mqtt_thread = threading.Thread(target=self.mqtt_loop, daemon=True)
        mqtt_thread.start()
        
        # Start message processor
        self.message_processor()


if __name__ == "__main__":
    agent = HermesAgent()
    agent.run()
