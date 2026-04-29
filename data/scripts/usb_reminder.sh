#!/bin/bash
# /data/scripts/usb_reminder.sh
# Solar-Sentinel Backup Reminder

# Check if backup exists in last 8 days
# We look for a backup directory on any USB-like mount
USB_MOUNT=$(lsblk -nr -o MOUNTPOINT,TRAN | grep "usb" | awk '{print $1}' | head -n 1)
if [ -z "$USB_MOUNT" ]; then
    USB_MOUNT=$(df -h | grep -E '/media/|/mnt/' | awk '{print $6}' | head -n 1)
fi

REMIND=false

if [ -z "$USB_MOUNT" ]; then
    REMIND=true
    REASON="USB Drive not detected."
else
    BACKUP_DIR="$USB_MOUNT/solar_sentinel_backups"
    if [ ! -d "$BACKUP_DIR" ]; then
        REMIND=true
        REASON="No backup directory found on USB."
    else
        LATEST_BACKUP=$(find "$BACKUP_DIR" -name "backup_*.tar.gz" -mtime -8)
        if [ -z "$LATEST_BACKUP" ]; then
            REMIND=true
            REASON="No backup found in the last 8 days."
        fi
    fi
fi

if [ "$REMIND" = true ]; then
    curl -s -d "USB Backup Reminder: $REASON Please ensure USB is plugged and backup is running." ntfy.sh/solar_sentinel_alerts > /dev/null 2>&1 || true
    mosquitto_pub -h localhost -t home/system/backup_status -m "REMINDER: $REASON" 2>/dev/null || true
fi
