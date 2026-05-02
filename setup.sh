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

if [ ! -f "/data/guard/eva_registry.json" ] && [ -f "/usr/share/solar-sentinel/data/guard/eva_registry.json" ]; then
    echo "Initializing EVA registry..."
    cp /usr/share/solar-sentinel/data/guard/eva_registry.json /data/guard/eva_registry.json
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
    for bucket in "solar_forecast" "system_state" "eva_nodes" "eva_patterns"; do
        echo "Checking for $bucket bucket..."
        if ! influx bucket list --token "$INFLUX_TOKEN" --org "$INFLUX_ORG" --host "$INFLUX_URL" 2>/dev/null | grep -q "$bucket"; then
            echo "Creating $bucket bucket..."
            influx bucket create --name "$bucket" --org "$INFLUX_ORG" --token "$INFLUX_TOKEN" --host "$INFLUX_URL" 2>/dev/null || \
            curl -s -X POST "$INFLUX_URL/api/v2/buckets" \
                -H "Authorization: Token $INFLUX_TOKEN" \
                -H "Content-Type: application/json" \
                -d "{\"name\":\"$bucket\",\"orgID\":\"$INFLUX_ORG\",\"retentionRules\":[]}" > /dev/null || \
            echo "Warning: Could not create $bucket bucket"
        else
            echo "$bucket bucket already exists"
        fi
    done
else
    echo "Warning: InfluxDB not ready. Buckets will be created on first run."
fi

# Ensure influxdb data directory exists
if [ ! -d "/data/influxdb/engine" ]; then
    mkdir -p /data/influxdb/engine
    chown solar:solar /data/influxdb/engine
fi

# Check for Gemini API Key
if [ -z "$GOOGLE_API_KEY" ]; then
    echo "WARNING: GOOGLE_API_KEY is not set. Hermes AI agent will not function correctly."
else
    echo "GOOGLE_API_KEY is set."
fi

echo "Setup complete."
