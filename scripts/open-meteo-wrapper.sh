#!/bin/bash
# Load env vars and start open-meteo
if [ -f /etc/open-meteo/config.env ]; then
    export $(grep -v '^#' /etc/open-meteo/config.env | xargs)
fi
exec /usr/local/bin/open-meteo
