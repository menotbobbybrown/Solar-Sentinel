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
