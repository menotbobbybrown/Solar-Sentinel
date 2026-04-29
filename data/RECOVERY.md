# Solar-Sentinel-AIO Disaster Recovery Guide

This guide covers scenarios for restoring the Solar-Sentinel-AIO system in case of hardware or software failure.

## Scenario 1: Laptop dead with USB backup
**Goal:** Restore the entire system on new hardware.
1. Install Ubuntu on the new laptop.
2. Run `laptop_hardening.sh` from the USB backup to prepare the host.
3. Install Docker and Docker Compose.
4. Clone the Solar-Sentinel-AIO repository.
5. Plug in the USB backup drive.
6. Locate the latest `backup_YYYYMMDD_HHMMSS.tar.gz` on the USB.
7. Restore the `/data` directory:
   ```bash
   tar -xzf /path/to/usb/solar_sentinel_backups/backup_xxxx.tar.gz -C /
   ```
8. Start the containers:
   ```bash
   docker-compose up -d
   ```

## Scenario 2: SSD failed with USB backup
**Goal:** Replace SSD and restore system.
1. Replace the failed SSD.
2. Reinstall Ubuntu.
3. Follow the same steps as Scenario 1.

## Scenario 3: Container broken, /data intact
**Goal:** Rebuild and restart the containerized services.
1. If only the container is corrupted but the host `/data` volume is intact:
   ```bash
   docker-compose down
   docker-compose pull
   docker-compose up -d --build
   ```
2. The `setup.sh` script will run on start and ensure configurations are in place.

## Scenario 4: USB missing, SSD survived
**Goal:** Restore InfluxDB data from daily snapshots.
1. If the SSD is fine but you lost the USB backup and need to restore InfluxDB:
2. Look in `/data/backups/influx_YYYYMMDD/` for daily snapshots.
3. Use the influx restore command:
   ```bash
   docker exec -it solar-sentinel influx restore /data/backups/influx_YYYYMMDD/ --token YOUR_TOKEN
   ```

## Manual Operations Quick Reference
- **Check Logs:** `docker logs solar-sentinel` or view files in `/data/logs/`
- **Restart Services:** `docker exec solar-sentinel supervisorctl restart all`
- **Manual Backup:** `docker exec solar-sentinel /data/scripts/usb_backup.sh`
- **InfluxDB CLI:** `docker exec -it solar-sentinel influx v1 shell` (or v2 commands)
- **Check Health:** `docker exec solar-sentinel /data/scripts/healthcheck.sh`

## First-Boot Checklist
- Ensure `INFLUX_TOKEN` is set in your environment or `docker-compose.yml`.
- Run `setup.sh` (usually automatic via entrypoint).
- Verify USB drive is mounted and detected by running `/data/scripts/usb_backup.sh`.
