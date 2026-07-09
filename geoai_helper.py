"""Helper functions to interact with the GeoAI plugin models and pipelines."""
import os
import shutil
import tempfile
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsField,
    QgsRectangle,
)
from qgis.utils import iface
from qgis.PyQt.QtCore import QVariant


def _clip_raster_to_canvas_extent(source_path: str, workspace: str) -> tuple:
    """Clip a raster to the current map canvas extent if the canvas is meaningfully smaller.

    Returns:
        (clipped_path, was_clipped): path to use (original or clipped), and a bool.
        The caller is responsible for deleting the temp file when was_clipped is True.
    """
    try:
        from osgeo import gdal
        gdal.UseExceptions()
        gdal.SetConfigOption('CPL_LOG', 'NUL')
    except ImportError:
        print("GDAL not available — skipping canvas clip.")
        return source_path, False

    canvas = iface.mapCanvas()
    if not canvas:
        return source_path, False

    canvas_extent = canvas.extent()
    canvas_crs = canvas.mapSettings().destinationCrs()

    # Open raster to get its CRS and extent
    ds = gdal.Open(source_path, gdal.GA_ReadOnly)
    if not ds:
        return source_path, False

    gt = ds.GetGeoTransform()
    raster_width = ds.RasterXSize
    raster_height = ds.RasterYSize
    raster_x_min = gt[0]
    raster_y_max = gt[3]
    raster_x_max = raster_x_min + gt[1] * raster_width
    raster_y_min = raster_y_max + gt[5] * raster_height
    raster_extent = QgsRectangle(raster_x_min, raster_y_min, raster_x_max, raster_y_max)
    raster_area = raster_extent.width() * raster_extent.height()
    ds = None  # close

    from osgeo import osr
    raster_srs = osr.SpatialReference()
    raster_srs.ImportFromWkt(gdal.Open(source_path, gdal.GA_ReadOnly).GetProjection())
    raster_wkt = raster_srs.ExportToWkt()
    raster_crs = QgsCoordinateReferenceSystem.fromWkt(raster_wkt)

    # Reproject canvas extent to raster CRS if needed
    clip_extent = canvas_extent
    if canvas_crs.authid() != raster_crs.authid() and raster_crs.isValid():
        try:
            xform = QgsCoordinateTransform(canvas_crs, raster_crs, QgsProject.instance())
            clip_extent = xform.transformBoundingBox(canvas_extent)
        except Exception as xform_err:
            print(f"CRS transform warning (proceeding without clip): {xform_err}")
            return source_path, False

    # Intersect canvas with raster bounds
    clipped_intersection = clip_extent.intersect(raster_extent)
    if clipped_intersection.isEmpty():
        print("Canvas extent does not overlap the raster — using full raster.")
        return source_path, False

    # Only clip if canvas covers less than 95% of raster (worthwhile savings)
    clip_area = clipped_intersection.width() * clipped_intersection.height()
    if raster_area > 0 and (clip_area / raster_area) > 0.95:
        print("Canvas covers >95% of raster — skipping clip (not worth it).")
        return source_path, False

    # Perform the clip
    clipped_path = os.path.join(workspace, f"_canvas_clip_{os.path.basename(source_path)}")
    if not clipped_path.endswith(".tif"):
        clipped_path = os.path.splitext(clipped_path)[0] + ".tif"

    warp_opts = gdal.WarpOptions(
        outputBounds=(
            clipped_intersection.xMinimum(),
            clipped_intersection.yMinimum(),
            clipped_intersection.xMaximum(),
            clipped_intersection.yMaximum(),
        ),
        creationOptions=["COMPRESS=LZW", "TILED=YES"],
    )
    result_ds = gdal.Warp(clipped_path, source_path, options=warp_opts)
    if result_ds is None or not os.path.exists(clipped_path):
        print("Canvas clip failed — falling back to full raster.")
        return source_path, False

    result_ds = None  # flush
    pct = round(clip_area / raster_area * 100, 1)
    print(f"Clipped raster to canvas extent ({pct}% of full image). Saved to: {clipped_path}")
    return clipped_path, True


def _ensure_geoai_on_path() -> None:
    """Ensure the geoai plugin package directory is on sys.path so its modules are importable.

    QGIS normally adds the plugins/ directory at startup, but when code runs
    through BADR's executor the path may not have been set yet. This is a safe
    no-op if geoai is already importable.
    """
    import sys
    try:
        import geoai  # noqa: F401 — already importable, nothing to do
        return
    except ImportError:
        pass

    import os
    # The plugins directory is one level above geoai_helper.py's package (BADR/)
    plugins_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if plugins_dir not in sys.path:
        sys.path.insert(0, plugins_dir)
        print(f"Added plugins directory to sys.path: {plugins_dir}")

    try:
        import geoai  # noqa: F401
    except ImportError as e:
        raise ImportError(
            f"Cannot import 'geoai' even after adding '{plugins_dir}' to sys.path. "
            f"Make sure the GeoAI plugin is installed and enabled in QGIS. Error: {e}"
        ) from e


def geoai_status() -> dict:
    """Check the GeoAI plugin setup and report what is available.

    Returns a dict with:
        - 'importable': bool — whether geoai package can be imported
        - 'venv_exists': bool — whether the ML virtual environment is set up
        - 'venv_path': str — path to the venv
        - 'venv_python': str — path to the venv Python executable
        - 'packages': dict — {package: installed_bool} for key ML packages
        - 'ready': bool — True if everything needed to run models is in place
        - 'issues': list[str] — human-readable list of problems found
    """
    _ensure_geoai_on_path()

    status = {
        "importable": False,
        "venv_exists": False,
        "venv_path": None,
        "venv_python": None,
        "packages": {},
        "ready": False,
        "issues": [],
    }

    try:
        from geoai.core.venv_manager import (
            venv_exists,
            get_venv_python_path,
            VENV_DIR,
        )
        status["importable"] = True
        status["venv_path"] = VENV_DIR
        status["venv_exists"] = venv_exists()
        status["venv_python"] = get_venv_python_path()

        if not status["venv_exists"]:
            status["issues"].append(
                f"GeoAI virtual environment not found at '{VENV_DIR}'. "
                "Open the GeoAI plugin panel and click 'Install Dependencies' to set it up."
            )
        else:
            import subprocess, json as _json, sys as _sys
            py = get_venv_python_path()
            if not os.path.exists(py):
                status["issues"].append(f"Venv Python executable not found: {py}")
            else:
                # Ask the venv to list installed packages
                check_code = (
                    "import importlib.util, json; "
                    "pkgs = ['deepforest','segment_geospatial','omniwatermask','moondream','torch','torchvision']; "
                    "print(json.dumps({p: importlib.util.find_spec(p) is not None for p in pkgs}))"
                )
                try:
                    result = subprocess.run(
                        [py, "-c", check_code],
                        capture_output=True, text=True, timeout=15
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        status["packages"] = _json.loads(result.stdout.strip())
                        missing = [k for k, v in status["packages"].items() if not v]
                        if missing:
                            status["issues"].append(
                                f"Missing packages in GeoAI venv: {', '.join(missing)}. "
                                "Open the GeoAI plugin and reinstall dependencies."
                            )
                    else:
                        status["issues"].append(f"Could not query venv packages: {result.stderr[:200]}")
                except subprocess.TimeoutExpired:
                    status["issues"].append("Timeout checking venv packages.")
                except Exception as pkg_err:
                    status["issues"].append(f"Package check error: {pkg_err}")

    except ImportError as ie:
        status["issues"].append(
            f"Cannot import geoai: {ie}. Ensure the GeoAI plugin is installed and enabled."
        )

    status["ready"] = status["importable"] and status["venv_exists"] and not status["issues"]

    # Print a human-readable summary
    if status["ready"]:
        print("✅ GeoAI is ready. All dependencies are installed.")
    else:
        print("⚠️ GeoAI is NOT ready:")
        for issue in status["issues"]:
            print(f"  • {issue}")

    return status


# PIL decompression bomb limit (pixels). We stay a little below the default 178M.
_PIL_MAX_PIXELS = 150_000_000


def _ensure_within_pil_limit(source_path: str, workspace: str) -> tuple:
    """If the raster exceeds PIL's decompression bomb limit, resample it down.

    Returns:
        (path_to_use, temp_path_or_None)
        If a resampled copy was created, the caller must delete it when done.
    """
    try:
        from osgeo import gdal
        gdal.UseExceptions()
        gdal.SetConfigOption('CPL_LOG', 'NUL')
        ds = gdal.Open(source_path, gdal.GA_ReadOnly)
        if not ds:
            return source_path, None
        w, h = ds.RasterXSize, ds.RasterYSize
        pixel_count = w * h
        ds = None
        if pixel_count <= _PIL_MAX_PIXELS:
            return source_path, None  # Already fine

        # Calculate scale factor to bring within limit
        scale = (_PIL_MAX_PIXELS / pixel_count) ** 0.5
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        print(
            f"Image is {w}x{h} ({pixel_count:,} px) — exceeds PIL limit. "
            f"Resampling to {new_w}x{new_h} for DeepForest..."
        )
        base = os.path.splitext(os.path.basename(source_path))[0]
        resampled_path = os.path.join(workspace, f"_resampled_{base}.tif")
        warp_opts = gdal.WarpOptions(
            width=new_w, height=new_h,
            resampleAlg=gdal.GRA_Bilinear,
            creationOptions=["COMPRESS=LZW", "TILED=YES"],
        )
        result_ds = gdal.Warp(resampled_path, source_path, options=warp_opts)
        if result_ds is None or not os.path.exists(resampled_path):
            print("Resample failed — proceeding with original (may hit PIL limit).")
            return source_path, None
        result_ds = None
        print(f"Resampled to: {resampled_path}")
        return resampled_path, resampled_path
    except Exception as e:
        print(f"Warning: pixel limit check failed ({e}). Proceeding anyway.")
        return source_path, None


def _resolve_raster_path(source) -> str:
    """Helper to resolve strings or layer objects to an absolute raster path."""
    if not source:
        # Fallback: try active layer if it's a raster
        layer = iface.activeLayer()
        if layer and isinstance(layer, QgsRasterLayer):
            return layer.source()
        raise ValueError("No raster source path provided and active layer is not a raster layer.")
    
    if isinstance(source, str):
        if os.path.exists(source):
            return source
        # Try layer name or ID lookup in QGIS
        layers = QgsProject.instance().mapLayersByName(source)
        if layers and isinstance(layers[0], QgsRasterLayer):
            return layers[0].source()
        layer = QgsProject.instance().mapLayer(source)
        if layer and isinstance(layer, QgsRasterLayer):
            return layer.source()
        return source
        
    if hasattr(source, "source"):
        val = source.source() if callable(source.source) else source.source
        return str(val)
        
    return str(source)

def run_moondream(
    mode: str,
    prompt: str = None,
    source_path: str = None,
    caption_length: str = "normal",
    model_name: str = "vikhyatk/moondream2",
    device: str = None,
) -> dict:
    """Helper to run Moondream analysis on a raster layer or map canvas snapshot.

    Args:
        mode (str): 'caption', 'query', 'detect', or 'point' (case-insensitive).
        prompt (str): Prompt or object description for query, detect, or point modes.
        source_path (str): Path to the raster image. If None, grabs the current map canvas as a PNG.
        caption_length (str): 'short', 'normal', or 'long'.
        model_name (str): Model name, default 'vikhyatk/moondream2'.
        device (str): Device to use, e.g., 'cuda', 'cpu', 'mps', or None.

    Returns:
        dict: The result dictionary from the worker.
    """
    try:
        _ensure_geoai_on_path()
        from geoai.core.moondream_subprocess import MoondreamSubprocessClient
    except ImportError as e:
        raise RuntimeError(
            f"Cannot import GeoAI Moondream client: {e}\n"
            "Run geoai_status() to diagnose. Make sure the GeoAI plugin is enabled "
            "and its dependencies are installed via the GeoAI panel."
        ) from e

    from .settings import Settings
    workspace = Settings.workspace_folder() or tempfile.gettempdir()
    os.makedirs(workspace, exist_ok=True)

    temp_image_created = False
    if not source_path:
        canvas = iface.mapCanvas()
        if not canvas:
            raise RuntimeError("Map canvas not available.")
        source_path = os.path.join(workspace, "moondream_canvas_snapshot.png")
        pixmap = canvas.grab()
        pixmap.save(source_path, "PNG")
        temp_image_created = True
        print(f"Captured current map canvas to {source_path}")

    client = MoondreamSubprocessClient(model_name=model_name, device=device)
    try:
        print(f"Initializing Moondream subprocess with model '{model_name}'...")
        client.initialize()

        mode_lower = mode.lower()
        print(f"Running Moondream in '{mode}' mode...")

        if mode_lower == "caption":
            res = client.caption(source_path, length=caption_length)
            if not isinstance(res, dict):
                res = {"caption": str(res)}
        elif mode_lower == "query":
            if not prompt:
                raise ValueError("Prompt is required for 'query' mode.")
            res = client.query(prompt, source=source_path)
            if not isinstance(res, dict):
                res = {"answer": str(res)}
        elif mode_lower == "detect":
            if not prompt:
                raise ValueError("Prompt (object type) is required for 'detect' mode.")
            res = client.detect(source_path, prompt)
        elif mode_lower == "point":
            if not prompt:
                raise ValueError("Prompt (object description) is required for 'point' mode.")
            res = client.point(source_path, prompt)
        else:
            raise ValueError(f"Unknown mode: {mode}")

        if isinstance(res, dict) and "vector_path" in res:
            src_geojson = res["vector_path"]
            if src_geojson and os.path.exists(src_geojson):
                clean_prompt = "".join([c if c.isalnum() or c in ("-", "_") else "_" for c in prompt])
                filename = f"moondream_{mode_lower}_{clean_prompt}.geojson"
                dest_geojson = os.path.join(workspace, filename)
                shutil.copy(src_geojson, dest_geojson)
                res["vector_path"] = dest_geojson

                layer_name = f"Moondream {mode.capitalize()}: {prompt}"
                layer = QgsVectorLayer(dest_geojson, layer_name, "ogr")
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    print(f"Loaded layer: {layer_name}")
                else:
                    print(f"Warning: Failed to load layer from {dest_geojson}")

        return res

    finally:
        client.close()
        if temp_image_created and os.path.exists(source_path):
            try:
                os.remove(source_path)
            except OSError:
                pass

def run_deepforest(
    source_path: str = None,
    mode: str = "Single Image",  # "Single Image" or "Large Tile"
    model_name: str = "weecology/deepforest-tree",
    revision: str = "main",
    patch_size: int = 400,
    patch_overlap: float = 0.25,
    score_threshold: float = 0.3,
    iou_threshold: float = 0.15,
    dataloader_strategy: str = "batch",
    batch_size: int = 4,
    device: str = None,
    output_path: str = None,
    clip_to_canvas: bool = True,
) -> dict:
    """Helper to run DeepForest crown/object detection on a raster image.

    When clip_to_canvas=True (default), the raster is automatically clipped to
    the current map canvas extent before inference, saving time on large imagery.
    """
    try:
        _ensure_geoai_on_path()
        from geoai.core.deepforest_subprocess import DeepForestSubprocessClient
    except ImportError as e:
        raise RuntimeError(
            f"Cannot import GeoAI DeepForest client: {e}\n"
            "Run geoai_status() to diagnose. Make sure the GeoAI plugin is enabled "
            "and its dependencies are installed via the GeoAI panel."
        ) from e

    from .settings import Settings
    workspace = Settings.workspace_folder() or tempfile.gettempdir()
    os.makedirs(workspace, exist_ok=True)

    source_path = _resolve_raster_path(source_path)

    _temp_clip = None
    if clip_to_canvas:
        source_path, was_clipped = _clip_raster_to_canvas_extent(source_path, workspace)
        if was_clipped:
            _temp_clip = source_path

    _temp_resample = None
    source_path, _temp_resample = _ensure_within_pil_limit(source_path, workspace)

    print(f"Running DeepForest on: {source_path}")

    client = DeepForestSubprocessClient(model_name=model_name, revision=revision, device=device)
    try:
        client.initialize()
        print("Running DeepForest worker prediction...")
        res = client.predict_subprocess(
            image_path=source_path,
            mode=mode,
            patch_size=int(patch_size),
            patch_overlap=float(patch_overlap),
            iou_threshold=float(iou_threshold),
            dataloader_strategy=dataloader_strategy,
            score_threshold=float(score_threshold),
            batch_size=int(batch_size),
        )

        predictions = res.get("predictions", {})
        records = predictions.get("records", [])
        if not records:
            print("No DeepForest detections found.")
            return {"status": "no detections", "predictions": res}

        # Setup vector conversion via QGIS native classes
        raster_layer = QgsRasterLayer(source_path, "temp_df")
        if not raster_layer.isValid():
            raise RuntimeError(f"Cannot load raster for coordinate transformation: {source_path}")

        crs = raster_layer.crs()
        extent = raster_layer.extent()
        width = raster_layer.width()
        height = raster_layer.height()

        pixel_width = extent.width() / width
        pixel_height = extent.height() / height
        x_min_map = extent.xMinimum()
        y_max_map = extent.yMaximum()

        mem_layer = QgsVectorLayer(f"Polygon?crs={crs.authid()}", "DeepForest Detections", "memory")
        provider = mem_layer.dataProvider()
        provider.addAttributes([QgsField("score", QVariant.Double), QgsField("label", QVariant.String)])
        mem_layer.updateFields()

        features = []
        for rec in records:
            px_min, py_min, px_max, py_max = rec["xmin"], rec["ymin"], rec["xmax"], rec["ymax"]
            mx_min = x_min_map + px_min * pixel_width
            mx_max = x_min_map + px_max * pixel_width
            my_min = y_max_map - py_max * pixel_height
            my_max = y_max_map - py_min * pixel_height

            geom = QgsGeometry.fromRect(QgsRectangle(mx_min, my_min, mx_max, my_max))
            feat = QgsFeature()
            feat.setGeometry(geom)
            feat.setAttributes([float(rec.get("score", 0.0)), str(rec.get("label", "tree"))])
            features.append(feat)

        provider.addFeatures(features)
        mem_layer.updateExtents()

        if not output_path:
            clean_name = os.path.splitext(os.path.basename(source_path))[0]
            output_path = os.path.join(workspace, f"deepforest_detections_{clean_name}.gpkg")

        from qgis.core import QgsVectorFileWriter
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG" if output_path.endswith(".gpkg") else ("ESRI Shapefile" if output_path.endswith(".shp") else "GeoJSON")

        _write_res = QgsVectorFileWriter.writeAsVectorFormatV3(
            mem_layer,
            output_path,
            QgsProject.instance().transformContext(),
            options
        )
        write_result, err = _write_res[0], _write_res[1]
        if write_result != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"Failed to write vector output file: {err}")

        lname = f"DeepForest: {os.path.basename(source_path)}"
        vector_layer = QgsVectorLayer(output_path, lname, "ogr")
        if vector_layer.isValid():
            from qgis.core import QgsFillSymbol
            symbol = QgsFillSymbol.createSimple({
                "color": "0,255,0,50",
                "outline_color": "0,255,0,255",
                "outline_width": "0.4"
            })
            if vector_layer.renderer():
                vector_layer.renderer().setSymbol(symbol)
            QgsProject.instance().addMapLayer(vector_layer)
            print(f"Loaded layer: {lname}")

        return {
            "status": "success",
            "vector_path": output_path,
            "vector_feature_count": len(features),
            "predictions": res
        }

    finally:
        client.close()
        if _temp_clip and os.path.exists(_temp_clip):
            try:
                os.remove(_temp_clip)
            except OSError:
                pass
        if _temp_resample and os.path.exists(_temp_resample):
            try:
                os.remove(_temp_resample)
            except OSError:
                pass

def run_samgeo(
    source_path: str = None,
    prompt: str = None,
    point_coords: list = None,
    point_labels: list = None,
    box_coords: list = None,
    model_version: str = "SamGeo",  # "SamGeo", "SamGeo2", "SamGeo3"
    backend: str = "vit_h",
    device: str = None,
    confidence: float = 0.9,
    enable_interactive: bool = False,
    min_size: int = None,
    max_size: int = None,
    output_format: str = "vector",  # "vector" or "raster"
    vector_mode: str = "simple",   # "simple", "smooth", "regularize"
    min_area: float = 0.0,
    smooth_iterations: int = 3,
    epsilon: float = 2.0,
    output_path: str = None,
    clip_to_canvas: bool = True,
) -> dict:
    """Helper to segment anything in imagery using Segment Anything (SAM) models.

    When clip_to_canvas=True (default), the raster is automatically clipped to
    the current map canvas extent before inference, saving time on large imagery.
    """
    try:
        _ensure_geoai_on_path()
        from geoai.core.samgeo_subprocess import SamGeoSubprocessClient
    except ImportError as e:
        raise RuntimeError(
            f"Cannot import GeoAI SAMGeo client: {e}\n"
            "Run geoai_status() to diagnose. Make sure the GeoAI plugin is enabled "
            "and its dependencies are installed via the GeoAI panel."
        ) from e

    from .settings import Settings
    workspace = Settings.workspace_folder() or tempfile.gettempdir()
    os.makedirs(workspace, exist_ok=True)

    source_path = _resolve_raster_path(source_path)

    _temp_clip = None
    if clip_to_canvas:
        source_path, was_clipped = _clip_raster_to_canvas_extent(source_path, workspace)
        if was_clipped:
            _temp_clip = source_path

    print(f"Running SAM on: {source_path}")

    client = SamGeoSubprocessClient(
        model_version=model_version,
        backend=backend,
        device=device,
        confidence=confidence,
        enable_interactive=enable_interactive,
    )
    temp_raster = None
    try:
        client.initialize()
        print("Setting raster image in SAM worker...")
        client.set_image(source_path)

        if prompt:
            print(f"Generating masks using text prompt: '{prompt}'...")
            res = client.generate_masks(prompt, min_size=min_size, max_size=max_size)
        elif point_coords:
            print("Generating masks using point prompt...")
            res = client.generate_masks_by_points(
                point_coords=point_coords,
                point_labels=point_labels,
            )
        elif box_coords:
            print("Generating masks using box prompt...")
            res = client.generate_masks_by_boxes([box_coords])
        else:
            raise ValueError("Either prompt, point_coords, or box_coords must be specified.")

        mask_count = res.get("mask_count", 0)
        print(f"SAM finished. Found {mask_count} mask(s).")
        if mask_count == 0:
            return {"status": "no masks found", "result": res}

        if output_format == "raster":
            if not output_path:
                output_path = os.path.join(workspace, f"sam_mask_{os.path.basename(source_path)}")
                if not output_path.endswith(".tif"):
                    output_path += ".tif"
            client.save_masks(output_path, unique=True)

            layer = QgsRasterLayer(output_path, os.path.basename(output_path))
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                print(f"Loaded raster layer: {os.path.basename(output_path)}")
        else:
            # Vector output format
            if not output_path:
                output_path = os.path.join(workspace, f"sam_vector_{os.path.basename(source_path)}")
                if not (output_path.endswith(".gpkg") or output_path.endswith(".shp") or output_path.endswith(".geojson")):
                    output_path += ".gpkg"

            temp_raster = tempfile.NamedTemporaryFile(suffix=".tif", delete=False).name
            client.save_masks(temp_raster, unique=True)

            from geoai.core.geoai_task_subprocess import run_geoai_task
            vec_format = "gpkg" if output_path.endswith(".gpkg") else ("shapefile" if output_path.endswith(".shp") else "geojson")

            print(f"Vectorizing masks using mode '{vector_mode}'...")
            if vector_mode == "smooth":
                run_geoai_task(
                    "smooth_vector",
                    {
                        "mask_path": temp_raster,
                        "output_path": output_path,
                        "smooth_iterations": int(smooth_iterations),
                        "min_area": float(min_area) if min_area > 0 else 0,
                    }
                )
            elif vector_mode == "regularize":
                run_geoai_task(
                    "vectorize_mask",
                    {
                        "mask_path": temp_raster,
                        "output_path": output_path,
                        "epsilon": float(epsilon),
                        "min_area": float(min_area) if min_area > 0 else None,
                    }
                )
            else:
                run_geoai_task(
                    "raster_to_vector",
                    {
                        "mask_path": temp_raster,
                        "output_path": output_path,
                        "min_area": float(min_area) if min_area > 0 else 0,
                        "output_format": vec_format,
                    }
                )

            layer = QgsVectorLayer(output_path, os.path.basename(output_path), "ogr")
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                print(f"Loaded vector layer: {os.path.basename(output_path)}")

        return {
            "status": "success",
            "mask_count": mask_count,
            "output_path": output_path,
            "result": res,
        }

    finally:
        client.close()
        if temp_raster and os.path.exists(temp_raster):
            try:
                os.remove(temp_raster)
            except OSError:
                pass
        if _temp_clip and os.path.exists(_temp_clip):
            try:
                os.remove(_temp_clip)
            except OSError:
                pass

def run_water_segmentation(
    source_path: str = None,
    band_order: str = "naip",  # "naip", "sentinel2", "landsat" or list [R, G, B, NIR]
    output_format: str = "vector",  # "vector", "raster", or "both"
    device: str = None,
    dtype: str = "float32",
    no_data_value: int = 0,
    patch_size: int = 1000,
    overlap_size: int = 300,
    use_osm_water: bool = True,
    use_osm_building: bool = True,
    use_osm_roads: bool = True,
    min_size: int = 10,
    smooth: bool = True,
    smooth_iterations: int = 3,
    output_path: str = None,
    clip_to_canvas: bool = True,
) -> dict:
    """Helper to segment water bodies from satellite/aerial imagery using OmniWaterMask.

    When clip_to_canvas=True (default), the raster is automatically clipped to
    the current map canvas extent before inference, saving time on large imagery.
    """
    try:
        _ensure_geoai_on_path()
        from geoai.core.geoai_task_subprocess import run_geoai_task
    except ImportError as e:
        raise RuntimeError(
            f"Cannot import GeoAI task runner: {e}\n"
            "Run geoai_status() to diagnose. Make sure the GeoAI plugin is enabled "
            "and its dependencies are installed via the GeoAI panel."
        ) from e

    from .settings import Settings
    workspace = Settings.workspace_folder() or tempfile.gettempdir()
    os.makedirs(workspace, exist_ok=True)

    source_path = _resolve_raster_path(source_path)

    _temp_clip = None
    if clip_to_canvas:
        source_path, was_clipped = _clip_raster_to_canvas_extent(source_path, workspace)
        if was_clipped:
            _temp_clip = source_path

    print(f"Running water segmentation on: {source_path}")

    # --- Band count validation ---
    # OmniWaterMask requires exactly 4 bands (R, G, B, NIR).
    # Check upfront so the error is clear rather than a cryptic subprocess crash.
    try:
        from osgeo import gdal as _gdal
        _gdal.UseExceptions()
        _ds = _gdal.Open(source_path, _gdal.GA_ReadOnly)
        if _ds:
            band_count = _ds.RasterCount
            _ds = None
            if band_count < 4:
                raise ValueError(
                    f"Water segmentation (OmniWaterMask) requires a raster with at least 4 bands "
                    f"(Red, Green, Blue, NIR), but '{os.path.basename(source_path)}' only has {band_count} band(s).\n"
                    f"\nOptions:\n"
                    f"  • Use a multispectral image with a NIR band (e.g. Sentinel-2, Landsat, NAIP).\n"
                    f"  • For RGB-only imagery, try 'detect water bodies using SAM' or use DeepForest instead.\n"
                    f"  • If your raster has NIR in a non-standard band position, pass "
                    f"band_order=[R_idx, G_idx, B_idx, NIR_idx] (1-based) explicitly."
                )
            if isinstance(band_order, str):
                # Map preset names to 1-based [R, G, B, NIR] indices
                _preset_map = {
                    "naip":      [1, 2, 3, 4],
                    "sentinel2": [4, 3, 2, 8],
                    "landsat":   [4, 3, 2, 5],
                }
                if band_order.lower() in _preset_map:
                    resolved = _preset_map[band_order.lower()]
                    # Clamp to available bands
                    if max(resolved) > band_count:
                        print(
                            f"Warning: preset '{band_order}' references band {max(resolved)} "
                            f"but raster only has {band_count} bands. Using sequential [1,2,3,4] fallback."
                        )
                        band_order = list(range(1, min(5, band_count + 1)))
                    else:
                        band_order = resolved
    except ValueError:
        raise  # Re-raise our own validation errors
    except Exception as band_check_err:
        print(f"Warning: could not verify band count ({band_check_err}). Proceeding anyway.")


    out_raster_path = None
    out_vector_path = None

    if output_format in ("raster", "both"):
        out_raster_path = output_path if output_path and output_path.endswith(".tif") else os.path.join(workspace, f"water_mask_{os.path.basename(source_path)}")
        if not out_raster_path.endswith(".tif"):
            out_raster_path += ".tif"

    if output_format in ("vector", "both"):
        if output_path and (output_path.endswith(".gpkg") or output_path.endswith(".shp") or output_path.endswith(".geojson")):
            out_vector_path = output_path
        else:
            out_vector_path = os.path.join(workspace, f"water_polygons_{os.path.basename(source_path)}")
            if not out_vector_path.endswith(".gpkg"):
                out_vector_path += ".gpkg"

    kwargs = {
        "input_path": source_path,
        "band_order": band_order,
        "batch_size": 1,
        "dtype": dtype,
        "no_data_value": no_data_value,
        "patch_size": patch_size,
        "overlap_size": overlap_size,
        "use_osm_water": use_osm_water,
        "use_osm_building": use_osm_building,
        "use_osm_roads": use_osm_roads,
        "min_size": min_size,
        "smooth": smooth,
        "smooth_iterations": smooth_iterations,
        "overwrite": True,
        "verbose": True,
    }

    if out_raster_path:
        kwargs["output_raster"] = out_raster_path
    if out_vector_path:
        kwargs["output_vector"] = out_vector_path
    if device:
        kwargs["device"] = device

    print("Running segment_water task in GeoAI worker...")
    res = run_geoai_task("segment_water", kwargs)

    # Load results to map
    if out_raster_path and os.path.exists(res.get("output_raster", "")):
        layer = QgsRasterLayer(res["output_raster"], os.path.basename(res["output_raster"]))
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            print(f"Loaded water mask: {os.path.basename(res['output_raster'])}")

    if out_vector_path and os.path.exists(res.get("output_vector", "")):
        layer = QgsVectorLayer(res["output_vector"], os.path.basename(res["output_vector"]), "ogr")
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            print(f"Loaded water polygons: {os.path.basename(res['output_vector'])}")

    return res

def run_geoai_task(action: str, params: dict) -> dict:
    """Run an arbitrary task inside the GeoAI virtual environment worker."""
    try:
        from geoai.core.geoai_task_subprocess import run_geoai_task as core_run_geoai_task
    except ImportError as e:
        raise RuntimeError(
            "The GeoAI plugin is not installed or enabled. Please install it in QGIS."
        ) from e
    
    print(f"Running GeoAI general task: '{action}'...")
    return core_run_geoai_task(action, params)
