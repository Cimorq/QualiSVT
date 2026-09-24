"""SQLite-backed cache for expensive per-video analysis results (auto-CRF, quality evals)."""
import hashlib
import json
import os
import shutil
import sqlite3

import core.config as config


def init_db():
    try:
        conn = sqlite3.connect(config.CACHE_FILE_DB, timeout=10.0)
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS cache (vid_hash TEXT, cache_type TEXT, set_hash TEXT, settings_json TEXT, data_json TEXT, PRIMARY KEY (vid_hash, cache_type, set_hash))'''
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"Database Error: {e}")


def get_video_fingerprint(filepath):
    """Return a fast, collision-resistant-enough fingerprint for cache invalidation.

    Uses file size + nanosecond mtime + the first and last 1 MiB. This keeps the
    cache check inexpensive while also detecting changes outside the file header.
    """
    if not filepath or not os.path.exists(filepath):
        return None
    try:
        stat = os.stat(filepath)
        chunk_size = 1024 * 1024
        hasher = hashlib.md5()
        hasher.update(f"{stat.st_size}_{getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1e9))}".encode("utf-8"))
        with open(filepath, "rb") as f:
            first = f.read(chunk_size)
            hasher.update(first)
            if stat.st_size > chunk_size:
                f.seek(max(0, stat.st_size - chunk_size))
                hasher.update(f.read(chunk_size))
        return hasher.hexdigest()
    except OSError:
        return None


def tool_fingerprint(*tools):
    """Identify the exact executables that produced a cached result (path, size, mtime).

    Swapping ffmpeg / FFVship / HandBrakeCLI for another build (e.g. a different SVT-AV1 fork)
    changes the scores and sizes, so cached results from the old binary must not be reused.
    """
    out = []
    for tool in tools:
        path = tool if tool and os.path.isfile(tool) else (shutil.which(tool) if tool else None)
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        out.append([os.path.basename(str(tool)), st.st_size if st else None, st.st_mtime_ns if st else None])
    return out


def get_settings_fingerprint(settings_dict):
    return hashlib.md5(json.dumps(settings_dict, sort_keys=True).encode("utf-8")).hexdigest()


def check_cache(filepath, settings_dict, cache_type="autocrf"):
    vid_hash = get_video_fingerprint(filepath)
    if not vid_hash:
        return None
    try:
        with sqlite3.connect(config.CACHE_FILE_DB, timeout=10.0) as conn:
            row = conn.cursor().execute("SELECT data_json FROM cache WHERE vid_hash=? AND cache_type=? AND set_hash=?",
                           (vid_hash, cache_type, get_settings_fingerprint(settings_dict))).fetchone()
            if row:
                return json.loads(row[0])
    except Exception as e:
        print(f"Error checking cache: {e}")
    return None


def save_cache(filepath, settings_dict, result_data, cache_type="autocrf"):
    vid_hash = get_video_fingerprint(filepath)
    if not vid_hash:
        return
    set_hash = get_settings_fingerprint(settings_dict)
    try:
        with sqlite3.connect(config.CACHE_FILE_DB, timeout=10.0) as conn:
            if cache_type == "crf_eval":
                row = conn.cursor().execute(
                    "SELECT data_json FROM cache WHERE vid_hash=? AND cache_type=? AND set_hash=?",
                    (vid_hash, cache_type, set_hash)
                ).fetchone()
                if row:
                    existing_data = json.loads(row[0])
                    if "scores" in result_data and "scores" in existing_data:
                        existing_data["scores"].update(result_data["scores"])
                    for k, v in result_data.items():
                        if k != "scores":
                            existing_data[k] = v
                    result_data = existing_data
            conn.cursor().execute(
                "INSERT OR REPLACE INTO cache (vid_hash, cache_type, set_hash, settings_json, data_json) VALUES (?, ?, "
                "?, ?, ?)",
                (vid_hash, cache_type, set_hash, json.dumps(settings_dict), json.dumps(result_data))
            )
            conn.commit()
    except Exception as e:
        print(f"Error saving cache: {e}")
