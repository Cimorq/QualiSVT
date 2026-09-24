"""GOP calculation, crop detection and filter-chain helpers."""
from collections import Counter
import os
import re
import subprocess
import tempfile
import time

import core.config as config
from core.fs_utils import silent_remove
from core.video_info import get_fps, parse_time
from collections import Counter


class MediaPrepMixin:
    def calculate_gop(self, input_file, total_duration):
        gop_mode = self.current_job_settings.get("gop_mode", "10-Second GOP")
        if gop_mode == "Encoder Default" or total_duration < 180.0:
            return None
        target_sec = float(self.current_job_settings.get("gop_custom_sec", "10")) if gop_mode == "Custom" else 10.0
        vf_string = self.current_job_settings.get("custom_vf", "").strip()
        fps_val = None
        if vf_string:
            match = re.search(r"fps=([\d\./]+)", vf_string)
            if match:
                val = match.group(1)
                fps_val = (float(val.split("/")[0]) / float(val.split("/")[1])) if "/" in val else float(val)
        if not fps_val:
            fps_val = get_fps(input_file)
        return int(round((fps_val if fps_val > 0 else 24.0) * target_sec))

    def get_actual_readable_duration(self, file_path):
        last_time = 0.0
        proc = None
        try:
            self.update_status("Status: Analyzing incomplete file duration...")
            proc = self._spawn_tracked(
                [config.FFMPEG_EXE, "-err_detect", "ignore_err", "-i", file_path, "-c", "copy", "-f", "null", "-"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace"
            )
            # [FIX] Loosened time pattern to allow H:MM:SS and HH:MM:SS with any digit count (long videos).
            time_pattern = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
            while True:
                line = proc.stderr.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)  # EOF but not reaped yet: don't spin
                    continue
                if match := time_pattern.search(line):
                    last_time = parse_time(match.group(1))
            proc.wait()
        except Exception:
            pass
        finally:
            self._release_process(proc)
        return last_time

    def concat_files(self, part1, part2, output):
        concat_txt = os.path.join(tempfile.gettempdir(), f"qual_concat_{int(time.time())}.txt")
        try:
            def safe_path(p):
                return os.path.abspath(p).replace(os.sep, "/").replace("'", "'\\''")

            with open(concat_txt, "w", encoding="utf-8") as f:
                f.write(f"file '{safe_path(part1)}'\nfile '{safe_path(part2)}'\n")
            cmd = [
                config.FFMPEG_EXE,
                "-err_detect",
                "ignore_err",
                "-fflags",
                "+genpts",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_txt,
                "-c",
                "copy",
                "-f",
                "mp4" if output.endswith(".incomplete") and self.current_job_settings.get(
                    "out_format"
                ) == "MP4" else "matroska",
                "-avoid_negative_ts",
                "make_zero",
                output
            ]
            self.update_status("Status: Concatenating (Fixing Timeline)...")
            res = self._run_tracked(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
            if res.returncode != 0 and not self.cancel_requested:
                self._record_failure(
                    "Concatenating resumed parts (FFmpeg)", cmd, res.returncode, res.stderr, console_tail=15
                )
            return res.returncode == 0 and os.path.exists(output)
        except Exception:
            return False
        finally:
            silent_remove(concat_txt)

    def detect_crop(self, input_file, duration):
        try:
            res = self._run_tracked(
                [
                    config.FFMPEG_EXE,
                    "-y",
                    "-ss",
                    str(max(0, duration * 0.2)),
                    "-i",
                    input_file,
                    "-t",
                    "2",
                    "-vf",
                    "cropdetect=24:16:0",
                    "-f",
                    "null",
                    "-"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace"
            )
            if matches := re.findall(r"crop=([0-9]+:[0-9]+:[0-9]+:[0-9]+)", res.stderr):
                return f"crop={Counter(matches).most_common(1)[0][0]}"
        except Exception:
            pass
        return None

    def get_reference_filters(self):
        filters = []
        if self.current_job_settings.get("auto_crop", False) and getattr(self, "current_crop", None):
            filters.append(self.current_crop)
        scale_val = self.current_job_settings.get("scale", "Original")
        if scale_val != "Original":
            mapping = {
                "4K": ("3840","2160"),
                "1440p": ("2560","1440"),
                "1080p": ("1920","1080"),
                "720p": ("1280","720"),
                "480p": ("854","480")
            }
            w, h = next((v for k, v in mapping.items() if k in scale_val), ("1920","1080"))
            filters.append(
                f"scale='min({w},iw)':'min({h},ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
            )
        return ",".join(filters)

    def get_exact_ref_filter(self):
        filters = []
        if self.current_job_settings.get("auto_crop", False) and getattr(self, "current_crop", None):
            filters.append(self.current_crop)
        if self.current_job_settings.get("deint", False):
            filters.append("yadif=1")
        return (",".join(filters) + ",") if filters else ""

    def get_sample_filters(self):
        vf_list = []
        if ref_vf := self.get_reference_filters():
            vf_list.append(ref_vf)
        if self.current_job_settings.get("deint", False):
            vf_list.append("yadif=1")
        if cust_vf := self.current_job_settings.get("custom_vf", "").strip():
            vf_list.append(cust_vf)
        return ",".join(vf_list)
