#!/usr/bin/env python3
"""Start the Kryta Construction Set:  python asura-cs/run_editor.py  (needs: pip install PySide6)

If the Python running this file has no PySide6 (on this PC the default `py` is a
32-bit 3.13, for which PySide6 has no wheels), the script relaunches itself with
another installed Python that has PySide6 (py -3.12, -3.11, -3.10). Only when none
works does it show an error - in a message box on Windows, so a double-click does
not end in a console window that vanishes.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_RELAUNCH_ENV = "KRYTA_CS_RELAUNCHED"
_CANDIDATE_VERSIONS = ("3.12", "3.11", "3.10")


def _show_error(text):
    sys.stderr.write(text + "\n")
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "Kryta Construction Set", 0x10)
        except Exception:
            pass


def _relaunch_with_other_python():
    """Return the child's exit code, or None when no other Python has PySide6."""
    if os.environ.get(_RELAUNCH_ENV):
        return None
    launcher = shutil.which("py")
    if not launcher:
        return None
    script = os.path.abspath(__file__)
    env = dict(os.environ)
    env[_RELAUNCH_ENV] = "1"
    for version in _CANDIDATE_VERSIONS:
        probe = [launcher, "-" + version, "-c", "import PySide6"]
        try:
            ok = subprocess.call(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
        except OSError:
            ok = False
        if ok:
            return subprocess.call([launcher, "-" + version, script] + sys.argv[1:], env=env)
    return None


try:
    import PySide6  # noqa: F401
except ImportError:
    code = _relaunch_with_other_python()
    if code is not None:
        raise SystemExit(code)
    _show_error(
        "The Kryta Construction Set needs PySide6, and no installed Python has it.\n\n"
        "This Python: %s (%s)\n\n"
        "Install it into a 64-bit Python 3.10-3.12, for example:\n"
        "    py -3.12 -m pip install PySide6" % (sys.executable, sys.version.split()[0])
    )
    raise SystemExit(1)

# Crash log: Python errors and native crashes (with every thread's stack) go to
# crash.log beside this file, so a window that closes on its own leaves a trace.
import faulthandler  # noqa: E402
import traceback  # noqa: E402

_CRASH_LOG = os.path.join(HERE, "crash.log")
try:
    _crash_file = open(_CRASH_LOG, "w", encoding="ascii", errors="replace")
    faulthandler.enable(_crash_file, all_threads=True)
except OSError:
    _crash_file = None


def _log_exception(kind, value, tb):
    text = "".join(traceback.format_exception(kind, value, tb))
    sys.stderr.write(text)
    if _crash_file is not None:
        _crash_file.write(text + "\n")
        _crash_file.flush()


sys.excepthook = _log_exception

try:
    from kryta_cs.ui.app import main as main_entry  # noqa: E402  (splash first)
except Exception:
    _log_exception(*sys.exc_info())
    _show_error("The editor could not start; see crash.log:\n" + _CRASH_LOG)
    raise SystemExit(1)

if __name__ == "__main__":
    try:
        code = main_entry()
    except Exception:
        _log_exception(*sys.exc_info())
        _show_error("The editor stopped with an error; see crash.log:\n" + _CRASH_LOG)
        code = 1
    raise SystemExit(code)
