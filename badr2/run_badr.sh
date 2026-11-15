#!/bin/sh
# Launch BADR 2 with the system QGIS Python (Linux / macOS).
cd "$(dirname "$0")" && exec python3 -m badr "$@"
