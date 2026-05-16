#!/bin/bash
set -e

redis-server --daemonize yes

exec supervisord -c /app/supervisord.conf
