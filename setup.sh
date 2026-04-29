#!/bin/bash
set -e

echo "Solar-Sentinel-AIO Setup"

# Create necessary directories
DIRS=(
    "/data/homeassistant"
    "/data/influxdb"
    "/data/grafana/logs"
    "/data/grafana/plugins"
    "/data/mosquitto/data"
    "/data/mosquitto/log"
    "/data/node-red"
    "/data/uptime-kuma"
    "/data/scripts"
    "/data/logs"
    "/data/backups"
    "/data/agent"
    "/data/ollama/models"
)

for dir in "${DIRS[@]}"; do
    if [ ! -d "$dir" ]; then
        echo "Creating directory: $dir"
        mkdir -p "$dir"
    fi
done

# Copy scripts from template to /data/scripts
if [ -d "/usr/share/solar-sentinel/data/scripts" ]; then
    cp -r /usr/share/solar-sentinel/data/scripts/* /data/scripts/
fi

# Copy RECOVERY.md
if [ -f "/usr/share/solar-sentinel/data/RECOVERY.md" ]; then
    cp /usr/share/solar-sentinel/data/RECOVERY.md /data/RECOVERY.md
fi

# Ensure all scripts are executable
chmod +x /data/scripts/*.sh

# Run cron setup
if [ -f "/data/scripts/setup_cron.sh" ]; then
    echo "Installing cron jobs..."
    /data/scripts/setup_cron.sh
fi

# Set permissions
chown -R solar:solar /data

# Initial configuration if missing
if [ ! -f "/data/homeassistant/configuration.yaml" ]; then
    echo "Initializing Home Assistant configuration..."
    mkdir -p /data/homeassistant
    cp -r /etc/homeassistant/* /data/homeassistant/
fi

if [ ! -f "/data/node-red/flows.json" ]; then
    echo "Initializing Node-RED flows..."
    mkdir -p /data/node-red
    cp /etc/node-red/flows.json /data/node-red/flows.json
fi

if [ ! -f "/data/uptime-kuma/monitors.json" ]; then
    echo "Initializing Uptime Kuma monitors..."
    mkdir -p /data/uptime-kuma
    cp /etc/uptime-kuma/monitors.json /data/uptime-kuma/monitors.json
fi

if [ ! -d "/data/guard" ]; then
    echo "Initializing Energy Guard state directory..."
    mkdir -p /data/guard
fi

# Hermes Agent Setup
if [ ! -f "/data/agent/hermes_history.json" ]; then
    echo "[]" > /data/agent/hermes_history.json
fi

# Copy hermes_agent.py if it exists in the project source
if [ -f "/home/engine/project/data/agent/hermes_agent.py" ]; then
    cp /home/engine/project/data/agent/hermes_agent.py /data/agent/hermes_agent.py
fi

if [ -f "/data/agent/hermes_agent.py" ]; then
    chmod +x /data/agent/hermes_agent.py
fi

# Ollama model pull (idempotent)
if [ ! -d "/data/ollama/models/blobs" ]; then
    echo "Pulling Ollama model hermes-3-llama3.1:8b-q4_K_M..."
    OLLAMA_MODELS=/data/ollama/models /usr/local/bin/ollama serve &
    OLLAMA_PID=$!
    # Wait for Ollama to start
    for i in {1..10}; do
        if curl -s http://localhost:11434/api/tags > /dev/null; then
            break
        fi
        sleep 2
    done
    OLLAMA_MODELS=/data/ollama/models /usr/local/bin/ollama pull hermes-3-llama3.1:8b-q4_K_M
    kill $OLLAMA_PID
fi

# InfluxDB init (placeholder for more complex init)
if [ ! -d "/data/influxdb/engine" ]; then
    echo "InfluxDB data directory is empty. It will be initialized on first run."
fi

echo "Setup complete."
