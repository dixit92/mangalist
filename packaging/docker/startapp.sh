#!/bin/sh
# Started by the base image's supervisor as the "app" user (USER_ID / GROUP_ID), with DISPLAY set.
set -u
export QT_QPA_PLATFORM=xcb
cd /config || exit 1
exec /opt/mangalist/venv/bin/python -m mangalist "$@"
