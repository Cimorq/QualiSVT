"""Importing this package registers every built-in plugin with plugins.base.manager.

To add a new plugin: drop a module in this package that defines a class
decorated with @manager.register_monitor or @manager.register_encoder, and
import it below (or extend load_user_plugins() to scan a folder at runtime).
"""
from plugins.base import manager, SystemMonitorPlugin, EncoderBackend  # noqa: F401

from plugins import monitor_ohm  # noqa: F401
from plugins import encoder_ffmpeg  # noqa: F401
from plugins import encoder_handbrake  # noqa: F401
from plugins import encoder_svtav1encapp  # noqa: F401


def load_user_plugins(folder: str):
    """Optional: import any *_plugin.py file in `folder` so its classes self-register.
    Not called automatically — wire this into main.py if/when user-droppable plugins
    (outside this codebase) are wanted."""
    import os
    import importlib.util
    if not folder or not os.path.isdir(folder):
        return
    for fname in os.listdir(folder):
        if fname.endswith("_plugin.py"):
            path = os.path.join(folder, fname)
            spec = importlib.util.spec_from_file_location(fname[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception as e:
                print(f"Failed to load plugin {fname}: {e}")
