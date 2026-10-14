"""Keep Python exceptions and native crash traces in the regular application."""
import faulthandler
from functools import wraps
import logging
import os
from pathlib import Path
import sys
import tempfile


LOG_PATH = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "PdfEditor" / "crash.log"
log = logging.getLogger("pdf-editor.crash")
log.addHandler(logging.NullHandler())
log.propagate = False
_stream = None


def install_crash_logging():
    global _stream
    if _stream is not None:
        return
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _stream = LOG_PATH.open("a", encoding="utf-8", buffering=1)
    except OSError as exc:
        if sys.stderr is not None:
            print(f"Cannot open crash log: {exc}", file=sys.stderr)
        return
    handler = logging.StreamHandler(_stream)
    handler.setFormatter(logging.Formatter("%(asctime)s pid=%(process)d %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    # Keep this stream open for the lifetime of the native fault handler.
    faulthandler.enable(file=_stream, all_threads=True)
    sys.excepthook = lambda kind, value, tb: log.error("Unhandled exception", exc_info=(kind, value, tb))
    if sys.stderr is None:
        sys.stderr = _stream
    log.info("START executable=%s Python=%s", sys.executable, sys.version)


def log_exceptions(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception:
            # Log before Shiboken's virtual-method error handler can crash.
            log.exception("Failed: %s", method.__qualname__)
            raise
    return wrapped
