# Solar-Sentinel-AIO v3

Advanced Energy Management & Backup System.

## Phase 3: USB Backup + 20-Year Hardening
This version includes comprehensive system hardening and a robust USB-based backup and recovery strategy designed for long-term reliability.

## Services Included
1. **Home Assistant** - Smart home automation platform.
2. **InfluxDB 2.7.4** - Time series database for energy metrics.
3. **Grafana 10.2.3** - Visualization and dashboards.
4. **Mosquitto 2.0.18** - MQTT broker.
5. **Node-RED 3.1.3** - Flow-based automation.
6. **Uptime Kuma 1.23.11** - Monitoring and uptime checks.
7. **Open-Meteo v1.2.1** - Self-hosted weather API.
8. **Energy Guard** - Custom energy monitoring and protection service.

## Operational Guide

### Maintenance Scripts
All maintenance scripts are located in `/data/scripts/`:

| Script | Description |
|--------|-------------|
| `usb_backup.sh` | Auto-detects USB, creates compressed backup of `/data`, rotates last 8 backups. |
| `healthcheck.sh` | Monitors SSD SMART status, disk usage, service health, and data freshness. |
| `usb_reminder.sh` | Notifies via ntfy if no backup has been performed in the last 8 days. |
| `setup_cron.sh` | Idempotently installs the system maintenance schedule. |
| `laptop_hardening.sh` | **(Host Only)** Hardens Ubuntu host for 24/7 solar sentinel operation. |

### Cron Schedule
Maintenance is automated via the following schedule:

| Task | Schedule | Command |
|------|----------|---------|
| USB Backup | Weekly (Sun 02:00) | `usb_backup.sh` |
| Health Check | Weekly (Sun 01:00) | `healthcheck.sh` (via supervisor) |
| InfluxDB Snapshot | Daily (03:00) | `influx backup` |
| Prune Snapshots | Daily (03:30) | Delete snapshots > 30 days |
| Log Rotation | Monthly (1st 04:00) | Truncate all `.log` files |
| USB Reminder | Weekly (Mon 09:00) | `usb_reminder.sh` |

## Host Hardening (20-Year Hardening)
To prepare a new laptop host for 24/7 operation:
1. Copy `/data/scripts/laptop_hardening.sh` to the host.
2. Run as root: `sudo bash laptop_hardening.sh`.
3. This will disable sleep/suspend, ignore lid close, disable swap, set timezone (Asia/Dubai), and configure unattended-upgrades.

## Disaster Recovery
See `/data/RECOVERY.md` for full details.

### Quick Restore Reference
1. Prepare host with `laptop_hardening.sh`.
2. Install Docker.
3. Plug in USB backup.
4. Restore data: `tar -xzf /path/to/usb/solar_sentinel_backups/backup_xxxx.tar.gz -C /`
5. Restart: `docker-compose up -d`

## Configuration

### Port Map
| Service | Port | External URL |
|---------|------|--------------|
| Home Assistant | 8123 | http://localhost:8123 |
| InfluxDB | 8086 | http://localhost:8086 |
| Grafana | 3000 | http://localhost:3000 |
| Mosquitto | 1883 | localhost:1883 |
| Node-RED | 1880 | http://localhost:1880 |
| Uptime Kuma | 3001 | http://localhost:3001 |
| Open-Meteo | 8080 | http://localhost:8080 |

### Environment Variables (First-Boot Checklist)
Ensure the following are set in your environment or `.env` file:
- `INFLUX_TOKEN`: Administrative token for InfluxDB.
- `NTFY_TOPIC`: Topic for ntfy alerts (default: `solar_sentinel_alerts`).
- `LATITUDE` / `LONGITUDE`: For solar forecast accuracy.
- `OPEN_METEO_URL`: URL for the weather service (default: `http://localhost:8080`).

## Talking to Hermes
Hermes is the natural language interface for Solar-Sentinel-AIO. You can interact with it using three methods:

1. **Node-RED Chat UI:** Navigate to `http://localhost:1880/ui` and select the **Hermes AI Chat** tab.
2. **MQTT Direct:** Publish a message to `solar/hermes/inbox` and subscribe to `solar/hermes/outbox`.
3. **ntfy Webhook:** Hermes responses are also sent as push notifications via the configured ntfy topic.

### Example Commands
- *"Hermes, what is the current system status?"*
- *"Lock the washing machine until further notice."*
- *"What's the solar forecast for tomorrow?"*
- *"Set the battery lockout threshold to 15%."*

### Intelligence Engine
Hermes uses the **Google Gemini API** (Pro model) with 12 native tools for real-time system interaction.
- Requires `GOOGLE_API_KEY` environment variable.
- Uses function calling for status, forecast, and control.
- Integrated with EVA (Energy Value Analysis) for per-device mapping.

## Monitoring & Alerts
- **MQTT**: Health metrics are published to `solar/system/health_metrics`.
- **ntfy**: Critical alerts are sent to `ntfy.sh/solar_sentinel_alerts`.
- **Logs**: Detailed logs available in `/data/logs/`.
