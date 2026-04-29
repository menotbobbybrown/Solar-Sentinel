#!/bin/bash
# /data/scripts/laptop_hardening.sh
# Solar-Sentinel Host Hardening Script
# IMPORTANT: This script MUST be run on the UBUNTU HOST, NOT inside a Docker container.

if [ -f /.dockerenv ]; then
    echo "ERROR: This script is running inside a Docker container. Please run it on the host machine."
    exit 1
fi

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

echo "Starting Solar-Sentinel Host Hardening (20-Year Hardening)..."

# 1. Disable sleep/suspend/hibernate
echo "Disabling sleep, suspend, and hibernation..."
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# 2. Lid close behavior: HandleLidSwitch=ignore
echo "Configuring lid close behavior..."
sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
# Also good practice for laptops
sed -i 's/^#\?HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf
sed -i 's/^#\?HandleLidSwitchDocked=.*/HandleLidSwitchDocked=ignore/' /etc/systemd/logind.conf
systemctl restart systemd-logind

# 3. Disable screen blanking (consoleblank=0)
echo "Disabling screen blanking..."
if ! grep -q "consoleblank=0" /etc/default/grub; then
    sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="consoleblank=0 /' /etc/default/grub
    update-grub
fi

# 4. Enable Docker auto-start
echo "Enabling Docker auto-start..."
systemctl enable docker

# 5. Install and enable smartd for SSD monitoring
echo "Installing and enabling smartmontools..."
apt-get update && apt-get install -y smartmontools
systemctl enable smartmontools
systemctl start smartmontools

# 6. Install cron
echo "Installing cron..."
apt-get install -y cron
systemctl enable cron
systemctl start cron

# 7. Configure unattended-upgrades (security-only)
echo "Configuring unattended-upgrades..."
apt-get install -y unattended-upgrades
cat <<EOF > /etc/apt/apt.conf.d/50unattended-upgrades
Unattended-Upgrade::Allowed-Origins {
    "\${distro_id}:\${distro_codename}-security";
};
Unattended-Upgrade::Package-Blacklist {
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::MinimalSteps "true";
Unattended-Upgrade::InstallOnShutdown "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF
echo 'APT::Periodic::Update-Package-Lists "1";' > /etc/apt/apt.conf.d/20auto-upgrades
echo 'APT::Periodic::Unattended-Upgrade "1";' >> /etc/apt/apt.conf.d/20auto-upgrades

# 8. Set timezone to Asia/Dubai
echo "Setting timezone to Asia/Dubai..."
timedatectl set-timezone Asia/Dubai

# 9. Disable swap
echo "Disabling swap..."
swapoff -a
sed -i '/swap/d' /etc/fstab

# 10. Docker container auto-restart update
echo "Setting Docker containers to auto-restart..."
docker update --restart=always solar-sentinel 2>/dev/null || echo "Warning: solar-sentinel container not found. Ensure it is named correctly."

echo "Host hardening completed successfully."
