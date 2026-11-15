## BADR 2.0.0

First release of BADR 2: a standalone, AI-first military planning and analysis GIS built on the QGIS engine. The original BADR QGIS plugin is unchanged and remains in the repository root.

### Downloads

| File | Use it when |
|---|---|
| `BADR-Setup.exe` | One installer with the QGIS 3.44 LTR runtime inside. No admin rights needed. |
| `BADR-2-windows-lite.zip` | QGIS 3.34 or newer is already installed. Unzip and run `BADR.exe`. |

The installer is not code-signed, so Windows SmartScreen warns on first run: choose **More info → Run anyway**. Start with **BADR (training demo)** from the Start menu.

### What is in it

- One-screen interface: Picture, Plan, Terrain, Intel, Imagery, Sensors and Tools modes, with the review queue and assistant chat in the right sidebar
- NATO APP-6D symbology with a searchable catalog of 1,647 entities and a palette weighted to sub-conventional operations
- Terrain analysis: viewshed, line of sight, mobility, routes, landing zones, range rings, reference grids, cordons
- Incident pattern analysis: hotspots, pattern of life, route exposure
- Universal receive-only sensor links: UDP, TCP, serial, HTTP, MQTT or file, with Cursor-on-Target, MAVLink, NMEA, ADS-B or JSON
- Google Earth Engine imagery, elevation and change detection, with open terrain tiles as a fallback
- Every QGIS processing algorithm, searchable and runnable by the operator or the assistant
- AI assistant using Claude or any OpenAI-compatible endpoint (including local models), with a zero-token fast path for routine orders and human review of everything the AI adds

### Verified for this release

Built and tested on Windows by the release workflow: the full test suite passes, and `BADR.exe` starts the demo scenario both with an installed QGIS and with only the bundled runtime.

### Not yet verified

- The installer wizard itself (the build tests the files it installs, not the click-through)
- A live AI conversation and live Earth Engine requests (no credentials in the build environment)
- Real drones, radios and serial devices; MQTT; video playback
- macOS

### Scope

Planning and analysis only. Sensor links are receive-only and BADR does not command platforms. Analysis results are planning estimates and must be confirmed before they are relied upon.

Licensed under the GNU GPL v2 or later (BADR 2 links QGIS).
