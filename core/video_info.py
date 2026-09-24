"""ffprobe-backed video inspection: dimensions, pix_fmt, duration, fps, chapters, time parsing."""
import json
import subprocess

import core.config as config


def shorten_filename(name, max_len=100):
    return name[:max_len - 20] + "..." + name[-15:] if len(name) > max_len else name


def get_video_dimensions(filename):
    try:
        res = subprocess.run(
            [
                config.FFPROBE_EXE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                filename
            ],
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
            check=True,
            creationflags=config.C_FLAGS
        )
        return tuple(map(int, res.stdout.strip().split("x")))
    except Exception:
        return None, None


def get_video_stream_size(filename):
    """Return the encoded byte size of the first video stream.

    Prefer ffprobe's stream-level size when the container exposes it. Some
    containers (notably many Matroska files) report `N/A`, so fall back to
    summing packet sizes for the first video stream. This avoids counting
    audio, subtitles, chapters, and container overhead in CRF size tests.
    """
    try:
        res = subprocess.run(
            [
                config.FFPROBE_EXE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=size",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                filename
            ],
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
            check=True,
            creationflags=config.C_FLAGS
        )
        value = res.stdout.strip()
        if value.isdigit():
            return int(value)
    except Exception:
        pass

    try:
        res = subprocess.run(
            [
                config.FFPROBE_EXE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "packet=size",
                "-of",
                "csv=p=0",
                filename
            ],
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
            check=True,
            creationflags=config.C_FLAGS
        )
        total = 0
        found = False
        for line in res.stdout.splitlines():
            value = line.strip()
            if value.isdigit():
                total += int(value)
                found = True
        return total if found else 0
    except Exception:
        return 0


def get_video_pix_fmt(filename):
    try:
        res = subprocess.run(
            [
                config.FFPROBE_EXE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=pix_fmt",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                filename
            ],
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
            check=True,
            creationflags=config.C_FLAGS
        )
        return res.stdout.strip() or None
    except Exception:
        return None


def has_audio_stream(filename):
    try:
        res = subprocess.run(
            [
                config.FFPROBE_EXE,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                filename
            ],
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
            creationflags=config.C_FLAGS
        )
        return bool(res.stdout.strip())
    except Exception:
        return False


def get_duration(filename):
    try:
        res = subprocess.run(
            [
                config.FFPROBE_EXE,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                filename
            ],
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
            check=True,
            creationflags=config.C_FLAGS
        )
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def get_total_frames(filename, run=None):
    """Return the decoded video frame count when ffprobe can provide it.

    -count_frames decodes the whole file, so it can take minutes. Pass `run`
    (EncoderApp._run_tracked) to keep that ffprobe pausable/cancellable.
    """
    cmd = [config.FFPROBE_EXE, "-v", "error", "-select_streams", "v:0",
           "-count_frames", "-show_entries", "stream=nb_read_frames",
           "-of", "csv=p=0", filename]
    try:
        if run is None:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, text=True, errors="replace", check=True, creationflags=config.C_FLAGS
            )
        else:
            res = run(cmd, stdout=subprocess.PIPE, text=True, errors="replace")
            if res.returncode != 0:
                return 0
        value = res.stdout.strip()
        return int(value) if value and value.isdigit() else 0
    except Exception:
        return 0


def get_fps(filename):
    """Return a representative FPS, preferring avg_frame_rate for VFR media."""
    try:
        res = subprocess.run(
            [config.FFPROBE_EXE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "default=noprint_wrappers=1", filename],
            stdout=subprocess.PIPE, text=True, errors="replace", check=True,
            creationflags=config.C_FLAGS
        )
        rates = {}
        for line in res.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                rates[key.strip()] = value.strip()

        # avg_frame_rate is the better representative for VFR; fall back to
        # r_frame_rate when it is unavailable or invalid.
        for key in ("avg_frame_rate", "r_frame_rate"):
            rate = rates.get(key, "")
            if not rate:
                continue
            try:
                if "/" in rate:
                    num, den = rate.split("/", 1)
                    num, den = float(num), float(den)
                    if den > 0 and num > 0:
                        return num / den
                else:
                    value = float(rate)
                    if value > 0:
                        return value
            except (ValueError, ZeroDivisionError):
                continue
    except Exception:
        pass
    return 24.0


def format_fps(fps):
    try:
        return f"{float(fps):.3f}"
    except (TypeError, ValueError):
        return "24.000"


def get_chapter_times(filename, start_ch, end_ch):
    """Resolve a 1-based chapter range safely, clamping both ends to the file."""
    try:
        res = subprocess.run(
            [config.FFPROBE_EXE, "-v", "error", "-print_format", "json",
             "-show_chapters", filename],
            stdout=subprocess.PIPE, text=True, errors="replace", check=True,
            creationflags=config.C_FLAGS
        )
        chapters = json.loads(res.stdout).get("chapters", [])
        if not chapters:
            return None, None

        total = len(chapters)
        start_num = max(1, min(int(start_ch), total))
        end_num = max(start_num, min(int(end_ch), total))

        start_time = float(chapters[start_num - 1].get("start_time", 0) or 0)
        end_time = float(chapters[end_num - 1].get("end_time", 0) or 0)
        return start_time, end_time
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, OSError, subprocess.SubprocessError):
        return None, None
    except Exception:
        return None, None


def parse_time(time_str):
    parts = str(time_str).split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 1:
            return float(parts[0])
    except ValueError:
        pass
    return 0.0


def format_duration(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{int(h)}h {int(m)}m {int(s)}s" if h > 0 else (f"{int(m)}m {int(s)}s" if m > 0 else f"{int(s)}s")
