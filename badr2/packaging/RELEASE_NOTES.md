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

### Assistant speed and tokens

- Greetings and acknowledgements are answered locally: no model call, no tokens.
- Per-message overhead cut from about 3,400 to about 2,000 tokens (shorter tool definitions and prompt).
- Most requests now finish in one model call instead of two.
- Default model is now the fastest tier (`claude-haiku-5-5`, Fast depth). Existing installs on the old default are switched once; change it in Settings if you want deeper analysis.
- Requests time out after 90 seconds with a clear message instead of hanging.

Token figures are estimates from prompt size; they have not been measured against a live model.

### Not yet verified

- A live AI conversation and live Earth Engine requests (no credentials in the build environment)
- The installer wizard click-through; real drones, radios and serial devices; MQTT; video; macOS
- The map does not tilt: playback pans and zooms on a 2D map

### Scope

Planning and analysis only. Sensor links are receive-only and BADR does not command platforms.
