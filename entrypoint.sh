#!/bin/bash

# Start cron daemon
/usr/sbin/cron -f &

# Keep container alive with sleep
while true; do
  sleep 60
done
