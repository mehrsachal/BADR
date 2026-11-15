## BADR 2.1.0

Responds to the first round of user feedback on 2.0.0.

### Downloads

| File | Use it when |
|---|---|
| `BADR-Setup.exe` | One installer with the QGIS 3.44 LTR runtime inside. No admin rights needed. Installs over 2.0.0. |
| `BADR-2-windows-lite.zip` | QGIS 3.34 or newer is already installed. Unzip and run `BADR.exe`. |

The installer is not code-signed: on first run choose **More info → Run anyway**.

### Fixed

- **Right-click did nothing.** The map now has a working context menu (attributes, zoom, affiliation, accept/reject, delete, viewshed, range rings, cordon, copy grid).
- **Could not select anything on the map.** Click selects, Ctrl+click adds, Shift+drag box-selects, drag pans, Delete removes.
- **Accept / Reject in the review queue.** Rebuilt and now covered by tests that click the real buttons; each action is confirmed in the log.
- **Map lag.** Live feeds are coalesced to one map update a second, the map keeps a per-layer render cache, and the picture is held in memory with an atomic autosave instead of writing to disk on every edit.

### New

- **Inspector**: the selected feature's attributes, editable in place.
- **Attribute tables** for every layer (double-click a layer): sort, select, edit, delete, zoom to selected.
- **Export to GIS formats**: GeoPackage, Shapefile, GeoJSON, KML, CSV, file geodatabase. NATO symbols export with their SIDC.
- **Draw mode**: your own point, line and area layers with custom columns and colours, saved with the operation.
- **Answers as a story**: the assistant replies in up to five short sentences; Play flies the map to each one in turn.
- **Loading feedback**: an instant splash with a progress bar at start-up, an activity indicator for every task, and downloads (elevation, OpenStreetMap, Earth Engine) run in the background.
- **Measure tool** (distance and bearing) and a map toolbar.
- Redesigned interface: neutral palette, drawn icons, denser layout.

### Catalog and geodatabases

- **Catalog** mode: create real Esri file geodatabases (`.gdb`, open directly in ArcGIS Pro) and GeoPackages.
- **Feature datasets**, **feature classes** (point, line, polygon), **tables**, **fields** with aliases, and
  **coded-value domains**; add and delete fields, rename, delete, import any map layer.
- Add feature classes to the map, draw into them from Draw mode, edit them in the attribute table.
- The layer list is now called **Contents**, as in ArcGIS: Catalog is what exists on disk, Contents is what is drawn.

### Assistant speed and tokens

Measured against the live Claude API (`claude-haiku-5-5`, Fast depth) on the demo scenario:

| Request | Time | Model calls | Cost |
|---|---|---|---|
| "hi" | instant | 0 (answered locally) | $0 |
| "put a hostile mortar team 2 km north of OBJ FOX and draw a 3 km range ring" | 2.7 s | 1 | $0.0007 |
| "which route is more exposed to IEDs?" | 3.4 s | 2 | $0.0007 |
| "brief me" | 7.4 s | 3 | $0.0006 |

- Each model call carries about 3,600 input tokens of fixed overhead (tool definitions and prompt), cached after the first.
- A one-line summary of the map rides with each request, so most orders need no separate "read the map" call.
- Relative locations work: "2 km north of OBJ FOX".
- Default model is the fastest tier; existing installs on the old default are switched once. Change it in Settings for deeper analysis.
- Requests time out after 90 seconds with a clear message instead of hanging.
- Fixed a decoding fault that could make a working connection report "could not reach the API".

### Not yet verified

- Live Earth Engine requests (no credentials in the build environment)
- Opening the created `.gdb` in ArcGIS itself (verified with GDAL only)
- The installer wizard click-through; real drones, radios and serial devices; MQTT; video; macOS
- The map does not tilt: playback pans and zooms on a 2D map

### Scope

Planning and analysis only. Sensor links are receive-only and BADR does not command platforms.
