#!/bin/sh
# Launch BADR 2 with the system QGIS Python (Linux / macOS).
exec python3 "$(dirname "$0")/boot.py" "$@"
