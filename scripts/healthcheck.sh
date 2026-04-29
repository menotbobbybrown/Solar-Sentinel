#!/bin/bash
set -e

# Health check for all services

check_http() {
    curl -f "http://localhost:$1" > /dev/null 2>&1
}

# Home Assistant
check_http 8123 || exit 1

# InfluxDB
check_http 8086/health || exit 1

# Grafana
check_http 3000/api/health || exit 1

# Node-RED
check_http 1880 || exit 1

# Uptime Kuma
check_http 3001 || exit 1

# Open-Meteo
check_http 8080 || exit 1

echo "All services are healthy"
exit 0
