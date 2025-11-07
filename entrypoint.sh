#!/bin/bash

# Start cron daemon
service cron start

# Keep container alive
tail -f /app/logs/cron.log
