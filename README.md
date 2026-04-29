# Solar-Sentinel-AIO v3

Phase 1 of Solar-Sentinel-AIO: a multi-stage Dockerfile with 8 services managed by supervisord.

## Services Included
1. **Home Assistant** (latest stable) - Smart home automation platform.
2. **InfluxDB 2.7.4** - Time series database for energy metrics.
3. **Grafana 10.2.3** - Visualization and dashboards.
4. **Mosquitto 2.0.18** - MQTT broker for device communication.
5. **Node-RED 3.1.3** - Flow-based programming for automation.
6. **Uptime Kuma 1.23.11** - Monitoring and uptime checks.
7. **Open-Meteo v1.2.1** - Self-hosted weather API.
8. **Energy Guard** - Custom energy monitoring and protection service.

## Architecture
- **Base Image**: Alpine 3.19
- **Service Manager**: supervisord
- **Build**: Multi-stage Docker build with SHA256 verification (where applicable).
- **Configuration**: All configurations are stored in `/etc/` and symlinked or copied as needed.
- **Persistence**: Data is stored in `/data/`.

## Phase 2: Predictive Energy Guard Brain
The Energy Guard Brain is a predictive decision engine that analyzes solar forecasts and battery state to protect energy reserves.

### Dashboards & URLs
- **Node-RED Dashboard**: [http://localhost:1880/ui](http://localhost:1880/ui)
- **Home Assistant**: [http://localhost:8123](http://localhost:8123)
- **Grafana**: [http://localhost:3000](http://localhost:3000)
- **Uptime Kuma**: [http://localhost:3001](http://localhost:3001)
- **Open-Meteo API**: [http://localhost:8080](http://localhost:8080)

### Alerts & Notifications (ntfy)
To receive mobile notifications:
1. Install the **ntfy** app on your phone.
2. Subscribe to the topic: `solar_sentinel_guard` (or your configured `NTFY_TOPIC`).
3. Ensure the `NTFY_SERVER` and `NTFY_TOPIC` environment variables are correctly set in your environment.

### Threshold Configuration
Thresholds can be adjusted live via the **Live Threshold Editor** flow in Node-RED:
- **WARN threshold**: Daily solar forecast below this triggers an ADVISORY. (Default: 2.5 kWh)
- **CRIT threshold**: Daily solar forecast below this + low battery triggers a WARNING. (Default: 1.5 kWh)
- **LOCKOUT threshold**: Minimum allowed daily yield before total appliance lockout. (Default: 0.8 kWh)
- **Storm battery target**: Target SOC to maintain when bad weather is detected. (Default: 95%)

### First-Boot Checklist
1. Verify all containers are running: `docker-compose ps`.
2. Access Node-RED and verify the flows are imported correctly.
3. Check `/data/logs/guard.log` to ensure the Energy Guard Brain has started and connected to MQTT/InfluxDB.
4. Set your `LATITUDE` and `LONGITUDE` in the environment variables for accurate solar forecasts.

### Port Map
| Service | Port |
|---------|------|
| Home Assistant | 8123 |
| InfluxDB | 8086 |
| Grafana | 3000 |
| Mosquitto | 1883 |
| Node-RED | 1880 |
| Uptime Kuma | 3001 |
| Open-Meteo | 8080 |

## Usage
### Build
```bash
docker build -t solar-sentinel-aio:v3 .
```

### Run
```bash
docker-compose up -d
```

## Security
- Services run as a non-root user `solar` where possible.
- Minimal Alpine-based image.
- Version pinning for all major components.
