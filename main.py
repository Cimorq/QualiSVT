"""QualiSVT entry point.

Startup sequence (see main()):
  1. resolve the external tool paths (ffmpeg, ffprobe, FFVship, ...) and create the Tk root,
  2. verify the required tools and choose where config/cache files live,
  3. initialise the cache database, register the plugins, build the GUI.

Every module reads tool paths and data locations through `core.config` at call time
(e.g. `config.FFMPEG_EXE`), so nothing depends on import order. The GUI and plugin
imports are still done inside main() so that any import error is caught by the
fatal-error dialog below.
"""
import datetime
import os
import sys
import traceback

import core.config as config


def _show_fatal_error(exc_text):
    import tkinter as tk
    from tkinter import messagebox
    try:
        log_path = os.path.join(config.APP_DIR, "QualiSVT_crash_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- Crash at {datetime.datetime.now()} ---\n{exc_text}\n")
    except Exception:
        log_path = None
    try:
        created_root = False
        err_root = getattr(tk, "_default_root", None)
        if err_root is None:
            err_root = tk.Tk()
            created_root = True
        try:
            err_root.withdraw()
        except Exception:
            pass
        msg = "QualiSVT crashed on startup:\n\n" + exc_text
        if log_path:
            msg += f"\n\n(Also saved to: {log_path})"
        messagebox.showerror("QualiSVT - Fatal Error", msg)
        if created_root:
            try:
                err_root.destroy()
            except Exception:
                pass
    except Exception:
        print(exc_text)


def check_dependencies(main_root):
    import shutil
    from tkinter import messagebox
    missing = []

    def _is_valid(path):
        return path and (os.path.isfile(path) or shutil.which(path))

    if not _is_valid(config.FFMPEG_EXE):
        missing.append("ffmpeg")
    if not _is_valid(config.FFPROBE_EXE):
        missing.append("ffprobe")
    if not _is_valid(config.FFVSHIP_EXE):
        missing.append("FFVship")

    if missing:
        error_msg = (
            "The following required tools were not found in your system PATH "
            "or 'Bin' folder next to the application:\n\n"
            + "\n".join(f" - {m}" for m in missing)
            + "\n\nPlease install them or place them in the 'Bin' folder before running."
        )
        messagebox.showerror("Missing Dependencies", error_msg, parent=main_root)
        return False
    return True


def main():
    import tkinter as tk

    config.resolve_tools()

    root = config.TkinterDnD.Tk() if config.HAS_DND else tk.Tk()
    root.withdraw()

    if not check_dependencies(root):
        sys.exit(1)

    config.DATA_DIR = config.resolve_data_dir(root)
    config.CONFIG_FILE = os.path.join(config.DATA_DIR, "QualiSVT_Config.json")
    config.CACHE_FILE_DB = os.path.join(config.DATA_DIR, "QualiSVT_Cache.db")

    from core.cache_db import init_db

    init_db()

    try:
        root.tk.call("source", "azure.tcl")
        root.tk.call("set_theme", "dark")
    except Exception:
        pass

    # Register all built-in plugins (monitors + encoder backends) before the
    # GUI is built, so any plugin-driven dropdowns can enumerate them.
    import plugins  # noqa: F401  (populates plugins.base.manager)

    from gui.encoder_app import EncoderApp

    app = EncoderApp(root)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        _show_fatal_error(traceback.format_exc())
        sys.exit(1)
