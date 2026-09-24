"""Centralised, mutable application configuration.

Tool paths, data locations and feature flags live here. Other modules do
`import core.config as config` and read `config.NAME` at call time, so they always see
the live value even though resolve_tools() / resolve_data_dir() change it during startup
(see main.py). Never copy these values with `from core.config import ...`.
"""
import os
import platform
import shutil
import subprocess
import sys
from tkinter import messagebox

try:
    import psutil  # noqa: F401
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # noqa: F401
    HAS_DND = True
except ImportError:
    HAS_DND = False

APP_VERSION = "1.0.2-R9"


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    try:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        return os.getcwd()


APP_DIR = get_app_dir()
BIN_DIR = os.path.join(APP_DIR, "Bin")
os.makedirs(BIN_DIR, exist_ok=True)

# Initialized after the Tk root is created (see main.py)
DATA_DIR = None
CONFIG_FILE = None
CACHE_FILE_DB = None

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v")
C_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IS_WINDOWS = platform.system() == "Windows"

CACHE_SCHEMA_VERSION = 3  # bump whenever the meaning/contents of the cache key change

# --- Tool Resolvers ---
FFMPEG_EXE, FFPROBE_EXE, FFVSHIP_EXE, HANDBRAKE_EXE = "ffmpeg", "ffprobe", "FFVship", "HandBrakeCLI"
# New: resolved lazily/optionally, used by the SvtAv1EncApp encoder plugin.
SVTAV1ENCAPP_EXE = "SvtAv1EncApp"


def resolve_data_dir(main_root):
    loc_file = os.path.join(BIN_DIR, "QualiSVT_Location.txt")
    if os.path.exists(loc_file):
        try:
            with open(loc_file, "r") as f:
                if f.read().strip() == "AppData":
                    return os.path.join(os.getenv("LOCALAPPDATA") or os.path.expanduser("~"), "QualiSVT")
                return BIN_DIR
        except Exception as e:
            print(f"Error reading location: {e}")

    ans = messagebox.askyesno(
        "Data Storage Location",
        "Would you like to store configuration and cache files in the user AppData folder?\n\nYes = AppData\n"
        "No = Portable Mode",
        parent=main_root
    )
    try:
        with open(loc_file, "w") as f:
            f.write("AppData" if ans else "Portable")
    except Exception:
        ans = True

    target_dir = os.path.join(os.getenv("LOCALAPPDATA") or os.path.expanduser("~"), "QualiSVT") if ans else BIN_DIR
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def resolve_tools():
    global FFMPEG_EXE, FFPROBE_EXE, FFVSHIP_EXE, HANDBRAKE_EXE, SVTAV1ENCAPP_EXE

    def _find(name):
        exe = name + (".exe" if os.name == "nt" else "")
        for base in (BIN_DIR, APP_DIR):
            p = os.path.join(base, exe)
            if os.path.isfile(p):
                return p
        return shutil.which(exe) or exe

    FFMPEG_EXE = _find("ffmpeg")
    FFPROBE_EXE = _find("ffprobe")
    FFVSHIP_EXE = _find("FFVship")
    HANDBRAKE_EXE = _find("HandBrakeCLI")
    # Optional: only used if the user selects the SvtAv1EncApp encoder plugin.
    # Not in check_dependencies() because the app must keep working without it.
    SVTAV1ENCAPP_EXE = _find("SvtAv1EncApp")
