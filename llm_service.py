"""Unified multi-provider LLM client."""
import json
import os
import uuid
import time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .settings import Settings, LlmProvider, PROVIDER_INFO

_SYSTEM_PROMPT_BASE = """\
You are a GIS assistant embedded in QGIS. You help users perform geospatial tasks
using natural language. You have access to the current map state (layers, extent, CRS).

IMPORTANT TERMINOLOGY:
If the user refers to their "screen", "my screen", or "current screen" (e.g., "download satellite imagery for my screen"), they mean the current map canvas extent. You must use the 'Current Extent' provided in the map state to limit your processing or data retrieval bounding boxes.

When the user asks you to perform a GIS operation, call the run_pyqgis function/tool
to generate and execute Python/PyQGIS code.

Guidelines for generated PyQGIS code:
- Access the current project: project = QgsProject.instance()
- Get the active canvas: canvas = iface.mapCanvas()
- Use processing.run() for geoprocessing: processing.run("native:buffer", {...})
- For layer references, use the layer name as shown in the map context
- CRITICAL — FILE OUTPUT: You will be given a BADR_WORKSPACE path in the map context.
  * ALWAYS save every output file (shapefiles, GeoTIFFs, CSVs, etc.) inside that folder.
  * Create a descriptive sub-folder for each operation, e.g.:
      out_dir = os.path.join(BADR_WORKSPACE, "buffer_results")
      os.makedirs(out_dir, exist_ok=True)
      out_path = os.path.join(out_dir, "roads_buffer_500m.shp")
  * Use processing.run() OUTPUT parameter to write directly to that path — NEVER use
    'memory:' or 'TEMPORARY_OUTPUT' for any final layer that should be kept.
  * For raster outputs: save as GeoTIFF (.tif) inside the workspace sub-folder.
  * After saving, load the layer from its file path:
      layer = QgsVectorLayer(out_path, 'Layer Name', 'ogr')
      QgsProject.instance().addMapLayer(layer)
    or for rasters:
      layer = QgsRasterLayer(out_path, 'Layer Name')
      QgsProject.instance().addMapLayer(layer)
- Use descriptive output names
- Print results/counts so the user gets feedback
- Handle errors with try/except and print useful messages
- Always end with a print() statement summarizing what was done and the saved file path
- For downloading files, use the built-in helper: download_with_progress(url, dest_path, title="Downloading...")
- For selections: layer.selectByExpression() or processing.run("native:selectbylocation", ...)
- Data Retrieval: Whenever the user asks for data (like shapefiles, boundaries, POIs) that isn't on the map, actively write Python code to fetch it from the internet (e.g., via the Overpass API for OSM data, or direct GeoJSON/Shapefile URLs) and load it into QGIS.

IMPORTANT: Use the record_lesson tool when you or the user identifies a new rule, best practice, or correction (e.g. 'Always ensure layers are in the same CRS before clipping'). This persists the lesson in your long-term memory for future sessions.

IMPORTANT — KNOWLEDGE BASE AND MEMORY:
You may be provided with "LONG-TERM MEMORY & LESSONS LEARNT" and/or "RELEVANT KNOWLEDGE BASE CONTEXT (RAG)".
- You MUST prioritize this provided information over your pre-existing knowledge.
- DO NOT assume data, file paths, algorithms, or attribute names if the knowledge base provides them.
- Strictly base your generated code and answers on this context when it is present.
- ANTI-HALLUCINATION RULE: When asked to filter, calculate, or analyze lists of data from the Knowledge Base, you MUST write Python code to programmatically parse and evaluate the data (e.g., using regex or string splitting). NEVER lazily hardcode the final answer based on your own reading of the text.

IMPORTANT \u2014 Error recovery:
When a tool execution returns an error, DO NOT just report the error to the user.
Instead, automatically try an alternative approach. For example:
- If a processing tool fails, try an alternative algorithm or workaround
- If a layer name is not found, list available layers and pick the closest match
- If a CRS transformation fails, try a different approach
Only report failure to the user after you have exhausted reasonable alternatives.

CRITICAL: In QGIS, sys.executable points to qgis-bin.exe, NOT python.exe.
NEVER use subprocess.run([sys.executable, ...]) \u2014 it launches a new QGIS instance.
For pip install, use: from pip._internal.cli.main import main as _pip; _pip(['install', 'package_name'])

SUGGESTED NEXT ACTIONS:
At the very end of your final response to the user, always provide up to 2 follow-up prompts that the user might want to run next. Wrap these suggestions in XML tags and separate them with a pipe character.
Example: <suggested_prompts>Buffer the roads layer | Clip the results to the city boundary</suggested_prompts>"""

_GEE_SECTION = """

Google Earth Engine Integration:
The user has GEE configured (project: {project}). You can generate Python code that uses
the earthengine-api (ee) to query, process, and download GEE data.

CRITICAL GEE DOWNLOAD RULES -- follow this EXACT pattern every time:
1. NEVER use `ee.batch.Export`. NEVER ask the user to monitor the Task Manager or manually download files from Google Drive. You must automate the ENTIRE process within the script.
2. NEVER call getDownloadURL without handling the 50 MB limit. NEVER degrade resolution.
3. Use native scale: S1 GRD=10, S2 B2/B3/B4/B8=10m, B5-B8A/B11/B12=20m, B1/B9/B10=60m.
4. ALWAYS use this adaptive tiled download with automatic retry:

```python
import ee, os, math, urllib.request
from osgeo import gdal
gdal.UseExceptions()
gdal.SetConfigOption('CPL_LOG', 'NUL')  # suppress noisy TIFF warnings
ee.Initialize(project='{project}')
# ... build image ...
SCALE = 10  # native resolution
region = [lon_min, lat_min, lon_max, lat_max]
import os
BADR_WORKSPACE = r'{workspace}'  # injected from BADR settings
out_dir = os.path.join(BADR_WORKSPACE, 'GEE_Download') if BADR_WORKSPACE else os.path.join(os.path.expanduser('~'), 'Documents', 'GEE_Download')
os.makedirs(out_dir, exist_ok=True)

def download_tiled(image, region, scale, out_dir, grid=1):
    lon_min, lat_min, lon_max, lat_max = region
    lat_step = (lat_max - lat_min) / grid
    lon_step = (lon_max - lon_min) / grid
    tile_paths = []
    
    total_tiles = grid * grid
    tile_idx = 0
    for r in range(grid):
        for c in range(grid):
            tile_region = ee.Geometry.Rectangle([
                lon_min + c*lon_step, lat_min + r*lat_step,
                lon_min + (c+1)*lon_step, lat_min + (r+1)*lat_step])
            tile_path = os.path.join(out_dir, f'tile_{{r}}_{{c}}.tif')
            try:
                url = image.getDownloadURL({{ 'scale': scale, 'region': tile_region,
                      'format': 'GEO_TIFF', 'filePerBand': False }})
                import threading
                from qgis.PyQt.QtCore import QCoreApplication
                tile_done = threading.Event()
                tile_err = []
                
                def reporthook(blocknum, blocksize, totalsize):
                    is_ab_cb = globals().get('is_aborted')
                    if is_ab_cb and is_ab_cb():
                        raise RuntimeError('Execution aborted by user.')
                    base_percent = (tile_idx * 100) / total_tiles
                    rep_prog = globals().get('report_progress')
                    if totalsize > 0:
                        download_percent = (blocknum * blocksize * 100) / (totalsize * total_tiles)
                        val = int(base_percent + download_percent)
                        if rep_prog: rep_prog('Downloading GEE Tiles', min(val, 100))
                    else:
                        if rep_prog: rep_prog('Downloading GEE Tiles', int(base_percent))
                
                def run_download():
                    try:
                        urllib.request.urlretrieve(url, tile_path, reporthook=reporthook)
                    except Exception as e:
                        tile_err.append(e)
                    finally:
                        tile_done.set()
                
                threading.Thread(target=run_download, daemon=True).start()
                while not tile_done.wait(0.02):
                    is_ab_cb = globals().get('is_aborted')
                    if is_ab_cb and is_ab_cb():
                        break
                    QCoreApplication.processEvents()
                if tile_err:
                    raise tile_err[0]
                
                tile_paths.append(tile_path)
                sz = os.path.getsize(tile_path) / 1e6
                print(f'[OK] tile_{{r}}_{{c}}.tif ({{sz:.1f}} MB)')
            except Exception as e:
                err = str(e)
                if 'must be less than or equal to' in err or '400' in err or 'Bad Request' in err:
                    print(f'[RETRY] Grid {{grid}}x{{grid}} too coarse -> {{grid*2}}x{{grid*2}}')
                    for p in tile_paths:
                        if os.path.exists(p): os.remove(p)
                    return download_tiled(image, region, scale, out_dir, grid*2)
                raise
            
            tile_idx += 1
            rep_prog = globals().get('report_progress')
            if rep_prog: rep_prog('Downloading GEE Tiles', int((tile_idx * 100) / total_tiles))
            
    return tile_paths

tile_paths = download_tiled(image, region, SCALE, out_dir)
merged = os.path.join(out_dir, 'result.tif')
gdal.Warp(merged, tile_paths)  # just merge, no extra options
for p in tile_paths: os.remove(p)
print(f'[DONE] {{merged}}')
```

4. After downloading, add the merged GeoTIFF as a raster layer to QGIS:
   iface.addRasterLayer(merged, 'layer_name')

IMPORTANT GDAL rules:
- Always call gdal.UseExceptions() and gdal.SetConfigOption('CPL_LOG', 'NUL') at the top.
- gdal.Warp(dest, srcs) -- do NOT pass options=['...'] as a list. Use keyword args if needed: gdal.Warp(dest, srcs, creationOptions=['COMPRESS=LZW']).

Other GEE rules:
- Server-side operations (mosaic, clip, compositing) have no size limit
- GEE List.get() only works for index 0-99. For collections >100 images, use .limit(N) or .aggregate_array() + .getInfo()
- Sentinel-1 system:index is the full product name (e.g. S1A_IW_GRDH_...), NOT a date. Use system:time_start (ms epoch) for dates.
- Use datetime.datetime.fromtimestamp(ts/1000, tz=datetime.timezone.utc) instead of deprecated utcfromtimestamp()"""


_GEOAI_SECTION = """

GeoAI Local Models and Pipelines Integration:
The user has the 'geoai' QGIS plugin installed and set up. This plugin provides access to local machine learning models for visual analysis and segmentation.
You can use the following built-in helper functions directly in your PyQGIS scripts:

CRITICAL RULES - READ BEFORE GENERATING ANY GEOAI CODE:

RULE 1 — NEVER DOUBT THE HELPERS:
The functions run_moondream, run_deepforest, run_samgeo, run_water_segmentation, run_geoai_task, and geoai_status
are ALWAYS pre-injected into every script you generate by BADR's code executor. You do NOT need to import them.
You do NOT need to check if geoai is installed before calling them. Just call them directly.
NEVER say "I cannot access DeepForest", "the geoai module does not expose this function", or "I cannot programmatically
run this model". You CAN always run them. If there is a failure, it will appear as a Python exception in the output.

RULE 2 — NEVER IMPORT FROM GEOAI DIRECTLY:
Do NOT write `from geoai.core import ...` or `import geoai` in any generated code.
The helpers abstract all of that. Only use the pre-injected functions listed below.

RULE 3 — NEVER GIVE UP, ALWAYS TRY:
If you are uncertain whether a model will work, still generate and run the code.
Let the runtime error tell you what went wrong. If it fails, THEN call geoai_status() to diagnose.

RULE 4 — TROUBLESHOOTING ONLY AFTER FAILURE:
If a GeoAI tool raises a RuntimeError or ImportError at runtime, your FIRST response must be to run:
    status = geoai_status()
    print(status)
Do NOT apologise and suggest manual alternatives before even trying. Run the code first.

0. GEOAI DIAGNOSTICS:
`geoai_status() -> dict`
- Checks whether the GeoAI virtual environment exists and all ML packages are installed.
- Returns dict with keys: 'ready' (bool), 'venv_exists', 'venv_path', 'venv_python', 'packages', 'issues'.
- Only run this AFTER a model function has already failed at runtime.

1. MOONDREAM VLM:
`run_moondream(mode: str, prompt: str = None, source_path: str = None, caption_length: str = "normal", model_name: str = "vikhyatk/moondream2", device: str = None) -> dict`
- `mode`: 'caption', 'query', 'detect', or 'point' (case-insensitive).
- `prompt`: Required for 'query' (question), 'detect' (object class to bounding box), and 'point' (object description).
- `source_path`: Image path. If None, snapshot of the map canvas is taken.
- Returns dict: `{"caption": "..."}`, `{"answer": "..."}` or `{"vector_path": "...", "vector_feature_count": N}` for detect/point (automatically loaded to QGIS).

2. DEEPFOREST (Tree Crown / Object Detection):
`run_deepforest(source_path: str = None, mode: str = "Single Image", model_name: str = "weecology/deepforest-tree", revision: str = "main", patch_size: int = 400, patch_overlap: float = 0.25, score_threshold: float = 0.3, iou_threshold: float = 0.15, dataloader_strategy: str = "batch", batch_size: int = 4, device: str = None, output_path: str = None, clip_to_canvas: bool = True) -> dict`
- `source_path`: Path to raster. If None, active layer or active canvas is used.
- `mode`: "Single Image" or "Large Tile" (recommended for high-res/tiled raster).
- `clip_to_canvas` (default True): Automatically clips the raster to the current map canvas extent before inference. This saves significant time on large satellite/aerial imagery. Set to False to process the full raster.
- `output_path`: Bounding box vector output path (.gpkg, .shp, or .geojson). If None, saved to BADR_WORKSPACE.
- Returns dict: `{"status": "success", "vector_path": "...", "vector_feature_count": N}`. Automatically loads the detections layer into QGIS.

3. SEGMENT ANYTHING (SAM / SAM2 / SAM3):
`run_samgeo(source_path: str = None, prompt: str = None, point_coords: list = None, point_labels: list = None, box_coords: list = None, model_version: str = "SamGeo", backend: str = "vit_h", device: str = None, confidence: float = 0.9, enable_interactive: bool = False, min_size: int = None, max_size: int = None, output_format: str = "vector", vector_mode: str = "simple", min_area: float = 0.0, smooth_iterations: int = 3, epsilon: float = 2.0, output_path: str = None, clip_to_canvas: bool = True) -> dict`
- `source_path`: Raster input.
- Prompts: Specify exactly one of `prompt` (text prompt for LangSAM, e.g. "tree"), `point_coords` (pixel coords `[[x,y], ...]`), or `box_coords` (pixel box `[x1,y1,x2,y2]`).
- `clip_to_canvas` (default True): Automatically clips the raster to the current map canvas extent before inference. Set to False to process the full raster.
- `output_format`: "vector" or "raster".
- `vector_mode`: Post-processing for output vectors: "simple", "smooth" (smooth edges), or "regularize" (orthogonalize for buildings).
- Returns dict: `{"status": "success", "mask_count": N, "output_path": "..."}`. Automatically loads the result layer into QGIS.

4. WATER SEGMENTATION (OmniWaterMask):
`run_water_segmentation(source_path: str = None, band_order: str = "naip", output_format: str = "vector", device: str = None, dtype: str = "float32", no_data_value: int = 0, patch_size: int = 1000, overlap_size: int = 300, use_osm_water: bool = True, use_osm_building: bool = True, use_osm_roads: bool = True, min_size: int = 10, smooth: bool = True, smooth_iterations: int = 3, output_path: str = None, clip_to_canvas: bool = True) -> dict`
- `source_path`: Raster input path.
- `band_order`: Preset string ("naip", "sentinel2", "landsat") or list of 4 band indices (e.g. `[1, 2, 3, 4]`).
- `clip_to_canvas` (default True): Automatically clips the raster to the current map canvas extent before inference. Set to False to process the full raster.
- `output_format`: "vector", "raster", or "both".
- `output_path`: File path to save output. If None, saves to BADR_WORKSPACE.
- Returns dict with `"output_raster"` and/or `"output_vector"`. Automatically loads result layers into QGIS.
- ⚠️ IMPORTANT: Requires a raster with AT LEAST 4 BANDS (R, G, B, NIR). Standard 3-band RGB images (e.g. JPEG, PNG, typical drone photos) will FAIL. Before calling this function, check how many bands the raster has. If the user has an RGB-only image, tell them water segmentation won't work and suggest using `run_samgeo(prompt="water")` or `run_samgeo(prompt="river")` as an alternative that works on any image.

5. GENERAL TASK RUNNER:
`run_geoai_task(action: str, params: dict) -> dict`
- Directly run tasks: "train_segmentation_model", "train_instance_segmentation_model", "export_geotiff_tiles", "semantic_segmentation", "instance_segmentation", "clip_raster_by_bbox", "raster_to_vector", "vectorize_mask", "smooth_vector".

NOTE - Canvas Auto-Clipping:
All three spatial models (DeepForest, SAMGeo, Water Segmentation) automatically clip the input raster to the
current map canvas extent before running inference (clip_to_canvas=True by default). This avoids processing
large satellite/aerial images unnecessarily. The clip is skipped if the canvas already covers >95% of the raster.
If the user explicitly wants to process the FULL image regardless of what's on screen, pass clip_to_canvas=False.

Examples:
1. Segment trees in active layer using LangSAM text prompt (auto-clipped to canvas):
   res = run_samgeo(prompt="tree")
   print(res)

2. Detect tree crowns using DeepForest on entire large image (no canvas clip):
   res = run_deepforest(source_path=r"C:/data/aerial.tif", mode="Large Tile", clip_to_canvas=False)
   print(f"Detected {res.get('vector_feature_count')} trees.")

3. Detect water bodies using NAIP imagery with OSM refinement (auto-clipped to canvas):
   res = run_water_segmentation(band_order="naip", output_format="vector")
   print(res)
"""





def build_system_prompt():
    """Build system prompt, appending GEE and GeoAI sections."""
    prompt = _SYSTEM_PROMPT_BASE
    gee_project = Settings.gee_project()
    workspace = Settings.workspace_folder()
    if gee_project:
        prompt += _GEE_SECTION.format(project=gee_project, workspace=workspace)
    prompt += _GEOAI_SECTION
    return prompt

RUN_PYQGIS_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "PyQGIS/Python code to execute"},
        "explanation": {"type": "string", "description": "Brief explanation of what this code does"},
    },
    "required": ["code", "explanation"],
}

RECORD_LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "lesson": {"type": "string", "description": "A concise, actionable rule or lesson learned from a mistake or user correction (e.g. 'Always check CRS compatibility before slope calculation')."},
    },
    "required": ["lesson"],
}


class LlmResponse:
    def __init__(self):
        self.text = ""
        self.tool_calls = []  # list of dicts with id, name, arguments

    @property
    def tool_call(self):
        """Convenience: first tool call or None."""
        return self.tool_calls[0] if self.tool_calls else None

    @property
    def has_tool_call(self):
        return len(self.tool_calls) > 0


MAX_HISTORY_LENGTH = 40


class LlmService:
    def __init__(self):
        self._history = []

    def clear_history(self):
        self._history.clear()

    def rollback_history(self, count=1):
        """Remove the last N entries from history."""
        for _ in range(min(count, len(self._history))):
            self._history.pop()

    def trim_history(self):
        """Trim history to MAX_HISTORY_LENGTH, preserving tool_use/tool_result pairs."""
        if len(self._history) <= MAX_HISTORY_LENGTH:
            return
        cut = len(self._history) - MAX_HISTORY_LENGTH
        while cut < len(self._history) - 2:
            msg = self._history[cut]
            if isinstance(msg.get("content"), list):
                has_tool = any(
                    isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result")
                    for b in msg["content"]
                )
                if has_tool:
                    cut += 1
                    continue
            if msg.get("tool_calls"):
                cut += 1
                continue
            if msg.get("role") == "tool":
                cut += 1
                continue
            break
        self._history = self._history[cut:]

    def send(self, user_message: str, map_context: str, image_base64: str = None, retry_callback=None) -> LlmResponse:
        self.trim_history()
        provider = Settings.provider()
        if PROVIDER_INFO[provider]["needs_key"] and not Settings.api_key():
            raise ValueError(
                f"API key not configured for {PROVIDER_INFO[provider]['display']}.\n"
                "Go to Plugins > BADR > Settings to configure."
            )
        
        # Prepare closures with retry_callback
        def send_anthropic(): return self._send_anthropic(user_message, map_context, image_base64, retry_callback)
        def send_gemini(): return self._send_gemini(user_message, map_context, image_base64, retry_callback)
        def send_openai(): return self._send_openai_compatible(user_message, map_context, image_base64, retry_callback)
        
        dispatch = {
            LlmProvider.Anthropic: send_anthropic,
            LlmProvider.GoogleGemini: send_gemini,
        }
        fn = dispatch.get(provider, send_openai)
        return fn()

    def send_tool_results(self, results: list, map_context: str, retry_callback=None) -> LlmResponse:
        self.trim_history()
        provider = Settings.provider()
        if provider == LlmProvider.Anthropic:
            return self._send_anthropic_tool_results(results, map_context, retry_callback)
        elif provider == LlmProvider.GoogleGemini:
            resp = LlmResponse()
            for tc_id, result in results:
                resp = self._send_gemini_tool_result(tc_id, result, map_context, retry_callback)
            return resp
        else:
            resp = LlmResponse()
            for tc_id, result in results:
                resp = self._send_openai_tool_result(tc_id, result, map_context, retry_callback)
            return resp

    # ---- HTTP helper ----

    @staticmethod
    def _post(url: str, headers: dict, body: dict, retry_callback=None) -> dict:
        data = json.dumps(body).encode("utf-8")
        req_factory = lambda: Request(url, data=data, method="POST")
        
        max_retries = 3
        base_delay = 5 # Start with 5s for rate limits
        
        for attempt in range(max_retries + 1):
            req = req_factory()
            for k, v in headers.items():
                req.add_header(k, v)
            req.add_header("Content-Type", "application/json")
            
            try:
                with urlopen(req, timeout=180) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as e:
                if e.code == 429 and attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    if retry_callback:
                        retry_callback(f"Rate limited. Waiting {delay}s...")
                    time.sleep(delay)
                    continue
                error_body = e.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"API error ({e.code}): {error_body}") from e
            except URLError as e:
                raise RuntimeError(f"Connection error: {e.reason}") from e
        
        raise RuntimeError("Maximum retries exceeded.")

    # ---- Anthropic ----
    def _send_anthropic(self, user_message, map_context, image_base64=None, retry_callback=None):
        if image_base64:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_base64
                    }
                },
                {"type": "text", "text": user_message}
            ]
        else:
            content = user_message

        self._history.append({"role": "user", "content": content})
        body = {
            "model": Settings.model(),
            "max_tokens": Settings.max_tokens(),
            "system": build_system_prompt() + "\n\n" + map_context,
            "messages": self._history,
            "tools": [
                {
                    "name": "run_pyqgis",
                    "description": "Execute PyQGIS/Python code in the QGIS Python environment.",
                    "input_schema": RUN_PYQGIS_SCHEMA,
                },
                {
                    "name": "record_lesson",
                    "description": "Record a new GIS lesson or best practice to long-term memory.",
                    "input_schema": RECORD_LESSON_SCHEMA,
                }
            ],
        }
        headers = {
            "x-api-key": Settings.api_key(),
            "anthropic-version": "2023-06-01",
        }
        data = self._post(Settings.effective_endpoint(), headers, body, retry_callback)
        self._history.append({"role": "assistant", "content": data.get("content", [])})
        return self._parse_anthropic(data)

    def _send_anthropic_tool_results(self, results, map_context, retry_callback=None):
        content = [
            {"type": "tool_result", "tool_use_id": tc_id, "content": result_text}
            for tc_id, result_text in results
        ]
        self._history.append({"role": "user", "content": content})
        body = {
            "model": Settings.model(),
            "max_tokens": Settings.max_tokens(),
            "system": build_system_prompt() + "\n\n" + map_context,
            "messages": self._history,
            "tools": [
                {
                    "name": "run_pyqgis",
                    "description": "Execute PyQGIS/Python code in the QGIS Python environment.",
                    "input_schema": RUN_PYQGIS_SCHEMA,
                },
                {
                    "name": "record_lesson",
                    "description": "Record a new GIS lesson or best practice to long-term memory.",
                    "input_schema": RECORD_LESSON_SCHEMA,
                }
            ],
        }
        headers = {
            "x-api-key": Settings.api_key(),
            "anthropic-version": "2023-06-01",
        }
        data = self._post(Settings.effective_endpoint(), headers, body, retry_callback)
        self._history.append({"role": "assistant", "content": data.get("content", [])})
        return self._parse_anthropic(data)

    @staticmethod
    def _parse_anthropic(data):
        resp = LlmResponse()
        for block in data.get("content", []):
            if block.get("type") == "text":
                resp.text += block.get("text", "")
            elif block.get("type") == "tool_use":
                resp.tool_calls.append({
                    "id": block["id"],
                    "name": block["name"],
                    "arguments": block["input"],
                })
        return resp

    # ---- OpenAI / Compatible ----
    def _send_openai_compatible(self, user_message, map_context, image_base64=None, retry_callback=None):
        if image_base64:
            content = [
                {"type": "text", "text": user_message},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"}
                }
            ]
        else:
            content = user_message

        self._history.append({"role": "user", "content": content})
        messages = [
            {"role": "system", "content": build_system_prompt() + "\n\n" + map_context},
        ] + self._history
        body = {
            "model": Settings.model(),
            "messages": messages,
            "max_tokens": Settings.max_tokens(),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "run_pyqgis",
                        "description": "Execute PyQGIS/Python code in the QGIS Python environment.",
                        "parameters": RUN_PYQGIS_SCHEMA,
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "record_lesson",
                        "description": "Record a new GIS lesson or best practice to long-term memory.",
                        "parameters": RECORD_LESSON_SCHEMA,
                    },
                }
            ],
        }
        headers = {}
        api_key = Settings.api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        data = self._post(Settings.effective_endpoint(), headers, body, retry_callback)
        choice = data["choices"][0]["message"]
        self._history.append(choice)
        return self._parse_openai(choice)

    def _send_openai_tool_result(self, tool_call_id, result, map_context, retry_callback=None):
        self._history.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})
        messages = [
            {"role": "system", "content": build_system_prompt() + "\n\n" + map_context},
        ] + self._history
        body = {
            "model": Settings.model(),
            "messages": messages,
            "max_tokens": Settings.max_tokens(),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "run_pyqgis",
                        "description": "Execute PyQGIS/Python code in the QGIS Python environment.",
                        "parameters": RUN_PYQGIS_SCHEMA,
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "record_lesson",
                        "description": "Record a new GIS lesson or best practice to long-term memory.",
                        "parameters": RECORD_LESSON_SCHEMA,
                    },
                }
            ],
        }
        headers = {}
        api_key = Settings.api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        data = self._post(Settings.effective_endpoint(), headers, body, retry_callback)
        choice = data["choices"][0]["message"]
        self._history.append(choice)
        return self._parse_openai(choice)

    @staticmethod
    def _parse_openai(message):
        resp = LlmResponse()
        resp.text = message.get("content") or ""
        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = tc["function"]
                resp.tool_calls.append({
                    "id": tc["id"],
                    "name": fn["name"],
                    "arguments": json.loads(fn.get("arguments", "{}")),
                })
        return resp

    # ---- Google Gemini ----
    def _send_gemini(self, user_message, map_context, image_base64=None, retry_callback=None):
        parts = [{"text": user_message}]
        if image_base64:
            parts.append({"inline_data": {"mime_type": "image/png", "data": image_base64}})
        
        self._history.append({"role": "user", "parts": parts})
        url = f"{Settings.effective_endpoint()}/models/{Settings.model()}:generateContent?key={Settings.api_key()}"
        contents = [
            {"role": "user", "parts": [{"text": build_system_prompt() + "\n\n" + map_context}]},
            {"role": "model", "parts": [{"text": "Understood."}]},
        ] + self._history
        body = {
            "contents": contents,
            "tools": [{
                "function_declarations": [
                    {
                        "name": "run_pyqgis",
                        "description": "Execute PyQGIS/Python code in the QGIS Python environment.",
                        "parameters": RUN_PYQGIS_SCHEMA,
                    },
                    {
                        "name": "record_lesson",
                        "description": "Record a new GIS lesson or best practice to long-term memory.",
                        "parameters": RECORD_LESSON_SCHEMA,
                    }
                ],
            }],
        }
        data = self._post(url, {}, body, retry_callback)
        parts = data["candidates"][0]["content"]["parts"]
        self._history.append({"role": "model", "parts": parts})
        return self._parse_gemini(parts)

    def _send_gemini_tool_result(self, tool_call_id, result, map_context, retry_callback=None):
        self._history.append({
            "role": "user",
            "parts": [{"functionResponse": {"name": "run_pyqgis", "response": {"result": result}}}],
        })
        url = f"{Settings.effective_endpoint()}/models/{Settings.model()}:generateContent?key={Settings.api_key()}"
        contents = [
            {"role": "user", "parts": [{"text": build_system_prompt() + "\n\n" + map_context}]},
            {"role": "model", "parts": [{"text": "Understood."}]},
        ] + self._history
        body = {"contents": contents}
        data = self._post(url, {}, body, retry_callback)
        parts = data["candidates"][0]["content"]["parts"]
        self._history.append({"role": "model", "parts": parts})
        return self._parse_gemini(parts)

    @staticmethod
    def _parse_gemini(parts):
        resp = LlmResponse()
        for part in parts:
            if "text" in part:
                resp.text += part["text"]
            if "functionCall" in part:
                fc = part["functionCall"]
                resp.tool_calls.append({
                    "id": "gemini_" + uuid.uuid4().hex[:8],
                    "name": fc["name"],
                    "arguments": fc["args"],
                })
        return resp
