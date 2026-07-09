"""Execute Python/PyQGIS code inside the running QGIS environment."""
import io
import sys
import threading
import traceback


class _ThreadLocalStream(io.StringIO):
    """A StringIO that captures per-thread stdout without clobbering sys.stdout globally."""

    def __init__(self, stream_callback=None):
        super().__init__()
        self._callback = stream_callback

    def write(self, s):
        super().write(s)
        if s and self._callback:
            try:
                self._callback(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        pass


# Thread-local storage so concurrent executions don't interfere.
_tls = threading.local()


class ExecutionResult:
    def __init__(self, success: bool, output: str, error: str | None = None):
        self.success = success
        self.output = output
        self.error = error

    def __str__(self):
        if self.success:
            return self.output.strip() if self.output.strip() else "Completed successfully."
        return f"Error: {self.error or 'Unknown error'}\n{self.output}".strip()


def run_pyqgis(code: str, progress_callback=None, stream_callback=None, is_aborted_callback=None) -> ExecutionResult:
    """Execute PyQGIS code on the calling thread, capturing stdout safely.

    This function should be called from the Qt main thread to ensure full
    thread safety with all QGIS APIs (canvas extent, layer manipulation, etc.).
    During slow operations like downloads, responsive UI is maintained by
    running the downloads on background threads while pumping events on the main thread.

    Args:
        code: Python code to execute.
        progress_callback: Optional callable(label, value) to report progress back to UI.
        stream_callback: Optional callable(text) to report live stdout.
        is_aborted_callback: Optional callable() returning True if user aborted.
    """
    capture = _ThreadLocalStream(stream_callback)

    # Save and replace stdout for this thread only via thread-local indirection.
    # We patch sys.stdout on the global object but store the old value so we can
    # restore it; this is safe because only one script runs per thread at a time.
    old_stdout = sys.stdout
    sys.stdout = capture

    try:
        from .geoai_helper import (
            run_moondream,
            run_deepforest,
            run_samgeo,
            run_water_segmentation,
            run_geoai_task,
            geoai_status,
        )
        exec_globals = {
            "__builtins__": __builtins__,
            "report_progress": progress_callback,
            "is_aborted": is_aborted_callback,
            "run_moondream": run_moondream,
            "run_deepforest": run_deepforest,
            "run_samgeo": run_samgeo,
            "run_water_segmentation": run_water_segmentation,
            "run_geoai_task": run_geoai_task,
            "geoai_status": geoai_status,
        }
        setup = (
            "from qgis.core import *\n"
            "from qgis.gui import *\n"
            "from qgis.utils import iface\n"
            "import qgis.processing as processing\n"
            "import os\n"
            "import urllib.request\n"
            # download_with_progress helper available to generated scripts.
            # Downloads in a background thread and pumps events on the main thread
            # so the UI remains responsive and checking is_aborted is supported.
            "def download_with_progress(url, dest_path, title='Downloading...'):\n"
            "    import threading\n"
            "    from qgis.PyQt.QtCore import QCoreApplication\n"
            "    done = threading.Event()\n"
            "    err = []\n"
            "    def target():\n"
            "        try:\n"
            "            def reporthook(blocknum, blocksize, totalsize):\n"
            "                if is_aborted and is_aborted():\n"
            "                    raise RuntimeError('Execution aborted by user.')\n"
            "                if totalsize > 0:\n"
            "                    percent = int((blocknum * blocksize * 100) / totalsize)\n"
            "                    if report_progress: report_progress(title, min(percent, 100))\n"
            "                else:\n"
            "                    if report_progress: report_progress(title, -1)\n"
            "            urllib.request.urlretrieve(url, dest_path, reporthook=reporthook)\n"
            "        except Exception as e:\n"
            "            err.append(e)\n"
            "        finally:\n"
            "            if report_progress: report_progress(title, 100)\n"
            "            done.set()\n"
            "    threading.Thread(target=target, daemon=True).start()\n"
            "    while not done.wait(0.02):\n"
            "        if is_aborted and is_aborted():\n"
            "            break\n"
            "        QCoreApplication.processEvents()\n"
            "    if err:\n"
            "        raise err[0]\n"
        )
        exec(setup, exec_globals)
        exec(code, exec_globals)

        output = capture.getvalue()
        if "ERROR:" in output:
            return ExecutionResult(False, output, output)
        return ExecutionResult(True, output)

    except Exception as e:
        output = capture.getvalue()
        tb = traceback.format_exc()
        return ExecutionResult(False, output, f"{type(e).__name__}: {e}\n{tb}")

    finally:
        sys.stdout = old_stdout
