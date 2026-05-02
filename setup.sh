#!/bin/bash
set -e

echo "Solar-Sentinel-AIO Setup"

# Create necessary directories
DIRS=(
    "/data/homeassistant"
    "/data/influxdb"
    "/data/influxdb/engine"
    "/data/grafana/logs"
    "/data/grafana/plugins"
    "/data/grafana/dashboards"
    "/data/mosquitto/data"
    "/data/mosquitto/log"
    "/data/node-red"
    "/data/uptime-kuma"
    "/data/scripts"
    "/data/logs"
    "/data/backups"
    "/data/agent"
    "/data/ollama/models"
    "/data/guard"
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

# Set proper ownership for directories before services start
echo "Setting directory permissions..."
chown -R solar:solar /data
chown solar:solar /data/grafana/dashboards
chown solar:solar /data/influxdb/engine
chown solar:solar /data/guard

# Run cron setup
if [ -f "/data/scripts/setup_cron.sh" ]; then
    echo "Installing cron jobs..."
    /data/scripts/setup_cron.sh
fi

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

# Ollama model pull (idempotent) with version check
if [ ! -d "/data/ollama/models/blobs" ]; then
    echo "Pulling Ollama model hermes-3-llama3.1:8b-q4_K_M..."
    OLLAMA_MODELS=/data/ollama/models /usr/local/bin/ollama serve &
    OLLAMA_PID=$!
    # Wait for Ollama to start
    for i in {1..30}; do
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo "Ollama started successfully"
            break
        fi
        echo "Waiting for Ollama to start... ($i/30)"
        sleep 2
    done
    OLLAMA_MODELS=/data/ollama/models /usr/local/bin/ollama pull hermes-3-llama3.1:8b-q4_K_M || echo "Warning: Failed to pull Ollama model"
    kill $OLLAMA_PID 2>/dev/null || true
fi

# InfluxDB bucket initialization
echo "Checking InfluxDB buckets..."
INFLUX_READY=0
for i in {1..30}; do
    if curl -s "http://localhost:8086/health" > /dev/null 2>&1; then
        echo "InfluxDB is healthy"
        INFLUX_READY=1
        break
    fi
    echo "Waiting for InfluxDB to start... ($i/30)"
    sleep 2
done

if [ "$INFLUX_READY" = "1" ]; then
    # Set InfluxDB token and org from environment or defaults
    INFLUX_TOKEN="${INFLUXDB_TOKEN:-my-token}"
    INFLUX_ORG="${INFLUXDB_ORG:-my-org}"
    INFLUX_URL="${INFLUXDB_URL:-http://localhost:8086}"
    
    # Check if buckets exist, create if missing
    echo "Checking for solar_forecast bucket..."
    if ! influx bucket list --token "$INFLUX_TOKEN" --org "$INFLUX_ORG" --host "$INFLUX_URL" 2>/dev/null | grep -q "solar_forecast"; then
        echo "Creating solar_forecast bucket..."
        influx bucket create --name solar_forecast --org "$INFLUX_ORG" --token "$INFLUX_TOKEN" --host "$INFLUX_URL" 2>/dev/null || \
        curl -s -X POST "$INFLUX_URL/api/v2/buckets" \
            -H "Authorization: Token $INFLUX_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"name\":\"solar_forecast\",\"orgID\":\"$INFLUX_ORG\",\"retentionRules\":[]}" > /dev/null || \
        echo "Warning: Could not create solar_forecast bucket (may already exist)"
    else
        echo "solar_forecast bucket already exists"
    fi
    
    echo "Checking for system_state bucket..."
    if ! influx bucket list --token "$INFLUX_TOKEN" --org "$INFLUX_ORG" --host "$INFLUX_URL" 2>/dev/null | grep -q "system_state"; then
        echo "Creating system_state bucket..."
        influx bucket create --name system_state --org "$INFLUX_ORG" --token "$INFLUX_TOKEN" --host "$INFLUX_URL" 2>/dev/null || \
        echo "Warning: Could not create system_state bucket (may already exist)"
    else
        echo "system_state bucket already exists"
    fi
    
    echo "Checking for eva_nodes bucket..."
    if ! influx bucket list --token "$INFLUX_TOKEN" --org "$INFLUX_ORG" --host "$INFLUX_URL" 2>/dev/null | grep -q "eva_nodes"; then
        echo "Creating eva_nodes bucket..."
        influx bucket create --name eva_nodes --org "$INFLUX_ORG" --token "$INFLUX_TOKEN" --host "$INFLUX_URL" 2>/dev/null || \
        echo "Warning: Could not create eva_nodes bucket (may already exist)"
    else
        echo "eva_nodes bucket already exists"
    fi
    
    echo "Checking for eva_patterns bucket..."
    if ! influx bucket list --token "$INFLUX_TOKEN" --org "$INFLUX_ORG" --host "$INFLUX_URL" 2>/dev/null | grep -q "eva_patterns"; then
        echo "Creating eva_patterns bucket..."
        influx bucket create --name eva_patterns --org "$INFLUX_ORG" --token "$INFLUX_TOKEN" --host "$INFLUX_URL" 2>/dev/null || \
        echo "Warning: Could not create eva_patterns bucket (may already exist)"
    else
        echo "eva_patterns bucket already exists"
    fi
else
    echo "Warning: InfluxDB not ready. Buckets will be created on first run."
fi

# Ensure influxdb data directory exists
if [ ! -d "/data/influxdb/engine" ]; then
    mkdir -p /data/influxdb/engine
    chown solar:solar /data/influxdb/engine
fi

echo "Setup complete."
