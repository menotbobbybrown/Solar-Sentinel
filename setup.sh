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
)

for dir in "${DIRS[@]}"; do
    if [ ! -d "$dir" ]; then
        echo "Creating directory: $dir"
        mkdir -p "$dir"
    fi
done

# Set permissions
chown -R solar:solar /data

# Initial configuration if missing
if [ ! -f "/data/homeassistant/configuration.yaml" ]; then
    echo "Initializing Home Assistant configuration..."
    mkdir -p /data/homeassistant
    cp -r /etc/homeassistant/* /data/homeassistant/
fi

# InfluxDB init (placeholder for more complex init)
if [ ! -d "/data/influxdb/engine" ]; then
    echo "InfluxDB data directory is empty. It will be initialized on first run."
fi

echo "Setup complete."
