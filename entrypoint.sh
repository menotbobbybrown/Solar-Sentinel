#!/bin/bash
set -e

# Run setup
/usr/local/bin/setup.sh

# Start supervisord
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
