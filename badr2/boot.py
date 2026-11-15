"""Start BADR 2 from a plain script path.

The Windows launcher (and run_badr.*) run this file instead of relying on the
PYTHONPATH environment variable, because the QGIS start-up scripts reset it.
Bundled packages in ``pydeps`` (AI SDK, Earth Engine, serial, MQTT) are added
after QGIS' own packages so they can never shadow the runtime's libraries.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
DEPS = os.path.join(HERE, "pydeps")
if os.path.isdir(DEPS):
    sys.path.append(DEPS)

if __name__ == "__main__":
    from badr.app import main
    sys.exit(main())
