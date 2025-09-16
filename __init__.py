
# Module-level shim so a pass runs even if the host doesn't call Plugin.autorun().
import threading, time, logging
from .plugin import Plugin

_log = logging.getLogger("plugins.channel_logo_auto_grab_auto_assign")

_started = False
def _kick():
    global _started
    if _started:
        return
    _started = True
    _log.info("[INFO] startup: module-level kick (12s)")
    def _bg():
        try:
            time.sleep(12)
            # best-effort: if the host already started autorun, this will no-op due to lock in plugin._do_pass
            Plugin().autorun(context={"shim": True})
        except Exception as e:
            _log.warning("[WARN] shim worker error: %s", e)
    th = threading.Thread(target=_bg, daemon=True)
    th.start()

_kick()

from .plugin import Plugin
