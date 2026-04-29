#!/bin/bash
# /data/scripts/healthcheck.sh
# Solar-Sentinel Health Watchdog

LOG_FILE="/data/logs/health.log"
mkdir -p /data/logs

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

STATUS="OK"
MESSAGES=()

# 1. SSD SMART health via smartctl
if command -v smartctl >/dev/null 2>&1; then
    # Checking /dev/sda as default, can be adjusted
    SMART_STATUS=$(smartctl -H /dev/sda | grep "test result" | awk '{print $NF}')
    if [ "$SMART_STATUS" != "PASSED" ] && [ -n "$SMART_STATUS" ]; then
        STATUS="CRITICAL"
        MESSAGES+=("SSD SMART Health: $SMART_STATUS")
    fi
else
    log "WARNING: smartctl not found, skipping SSD SMART check."
fi

# 2. Disk usage check
DISK_USAGE=$(df /data | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 95 ]; then
    STATUS="CRITICAL"
    MESSAGES+=("Disk Usage: ${DISK_USAGE}%")
elif [ "$DISK_USAGE" -gt 80 ]; then
    [ "$STATUS" != "CRITICAL" ] && STATUS="WARNING"
    MESSAGES+=("Disk Usage: ${DISK_USAGE}%")
fi

# 3. USB presence check
USB_MOUNT=$(lsblk -nr -o MOUNTPOINT,TRAN | grep "usb" | awk '{print $1}' | head -n 1)
if [ -z "$USB_MOUNT" ]; then
    # Also check common mount points
    if ! df -h | grep -E '/media/|/mnt/' > /dev/null; then
        [ "$STATUS" == "OK" ] && STATUS="WARNING"
        MESSAGES+=("USB Drive: NOT DETECTED")
    fi
fi

# 4. supervisord service status check + auto-restart if not RUNNING
if command -v supervisorctl >/dev/null 2>&1; then
    SERVICES=$(supervisorctl status | grep -v "RUNNING" | awk '{print $1}')
    for service in $SERVICES; do
        log "Service $service is not running. Attempting restart..."
        supervisorctl restart "$service"
        STATUS="WARNING"
        MESSAGES+=("Service Restarted: $service")
    done
fi

# 5. InfluxDB data freshness check (data within 30 min)
if command -v influx >/dev/null 2>&1 && [ -n "$INFLUX_TOKEN" ]; then
    LAST_DATA=$(influx query 'from(bucket:"solar") |> range(start: -1h) |> last()' --token "$INFLUX_TOKEN" 2>/dev/null)
    if [ -z "$LAST_DATA" ]; then
        [ "$STATUS" != "CRITICAL" ] && STATUS="WARNING"
        MESSAGES+=("InfluxDB Data: Stale (>30 min)")
    fi
fi

# Prepare MQTT payload
MSG_JOINED=$(IFS=,; echo "${MESSAGES[*]}")
[ -z "$MSG_JOINED" ] && MSG_JOINED="All systems nominal"
MQTT_PAYLOAD="{\"status\": \"$STATUS\", \"metrics\": \"$MSG_JOINED\", \"timestamp\": \"$(date -Iseconds)\"}"

# Publish health_metrics to MQTT
mosquitto_pub -h localhost -t home/system/health_metrics -m "$MQTT_PAYLOAD" 2>/dev/null || true

# Send ntfy alerts on errors/warnings
if [ "$STATUS" != "OK" ]; then
    log "$STATUS: $MSG_JOINED"
    curl -s -d "Health Alert ($STATUS): $MSG_JOINED" ntfy.sh/solar_sentinel_alerts > /dev/null 2>&1 || true
else
    log "Health Check: OK"
fi

if [ "$STATUS" == "CRITICAL" ]; then
    exit 1
fi
exit 0
