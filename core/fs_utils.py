"""Small filesystem helpers."""
import os


def silent_remove(path):
    """Delete *path* if possible; ignore every error (missing file, permissions, None, ...)."""
    try:
        os.remove(path)
    except Exception:
        pass
