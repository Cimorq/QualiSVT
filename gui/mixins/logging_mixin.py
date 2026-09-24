"""Console/log-file output and status/progress bar updates."""
import json
import os
import subprocess
import tkinter as tk

import core.config as config
from core.video_info import format_duration


class LoggingMixin:
    def log(self, message, tag="info"):
        def _write_log():
            self.console.config(state=tk.NORMAL)
            self.console.insert(tk.END, message + "\n", tag)
            self.console.see(tk.END)
            self.console.config(state=tk.DISABLED)

        self.ui_call(_write_log)

    def get_original_file_specs(self, input_file):
        try:
            res = subprocess.run(
                [
                    config.FFPROBE_EXE,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height,display_aspect_ratio,r_frame_rate,bit_rate:format=format_name,duration,bit_rate",
                    "-of",
                    "json",
                    input_file
                ],
                stdout=subprocess.PIPE,
                text=True,
                errors="replace",
                creationflags=config.C_FLAGS
            )
            data = json.loads(res.stdout)
            fmt = data.get("format", {})
            dur = float(fmt.get("duration", 0))
            v_codec = "Unknown"
            resolution = "Unknown"
            aspect_ratio = "N/A"
            framerate = "Unknown"
            bitrate = "Unknown"
            if streams := data.get("streams", []):
                vs = streams[0]
                v_codec = vs.get("codec_name", "Unknown").upper()
                if vs.get("width") and vs.get("height"):
                    resolution = f"{vs['width']}x{vs['height']}"
                if vs.get("display_aspect_ratio", "") not in ["", "0:1"]:
                    aspect_ratio = vs["display_aspect_ratio"]
                if fps := vs.get("r_frame_rate", ""):
                    framerate = f"{float(fps.split('/')[0])/float(fps.split('/')[1]):.3f} FPS" if "/" in fps and float(
                        fps.split("/")[1]
                    ) > 0 else f"{fps} FPS"
                if br := vs.get("bit_rate") or fmt.get("bit_rate"):
                    bitrate = f"{int(br)//1000} kbps"

            a_res = subprocess.run(
                [
                    config.FFPROBE_EXE,
                    "-v",
                    "error",
                    "-select_streams",
                    "a",
                    "-show_entries",
                    "stream=codec_name",
                    "-of",
                    "json",
                    input_file
                ],
                stdout=subprocess.PIPE,
                text=True,
                errors="replace",
                creationflags=config.C_FLAGS
            )
            a_codecs = list(
                set(
                    [
                        str(a.get("codec_name", "Unknown")).upper() for a in json.loads(a_res.stdout).get(
                            "streams", []
                        ) if str(a.get("codec_name", "Unknown")).upper() != "UNKNOWN"
                    ]
                )
            )

            return (
                " [ Original File Information ]\n"
                f"   - Container      : {fmt.get('format_name', 'Unknown').split(',')[0].strip().upper()}\n"
                f"   - Video Codec    : {v_codec}\n"
                f"   - Audio Codec(s) : {', '.join(a_codecs) if a_codecs else 'None'}\n"
                f"   - Resolution     : {resolution} ({aspect_ratio})\n   - Frame Rate     : {framerate}\n"
                f"   - Bitrate        : {bitrate}\n"
                f"   - Duration       : {format_duration(dur) if dur > 0 else 'Unknown'}\n"
                f"   - File Size      : {os.path.getsize(input_file)/(1024*1024):.2f} MB\n\n"
            )
        except Exception:
            return ""

    def save_current_log(self, log_file):
        if (
            not log_file
            or not self.current_log_data
            or self.current_job_settings.get("log_loc", "Next to Original") == "Don't Save"
        ):
            return
        d = self.current_log_data
        is_bench = self.current_job_settings.get("benchmark", False)
        final_log = (
            "======================================================================\n"
            f"{'                ANALYZE ONLY SUMMARY (NO OUTPUT GENERATED)            ' if is_bench else '                     ENCODING LOG'}\n"
            "======================================================================\n"
            f" File       : {d.get('file', 'Unknown')}\n Start Time : {d.get('start', 'Unknown')}\n"
            f" End Time   : {d.get('end', 'Running...')}\n\n"
        )
        for k in ["enc", "orig_specs", "settings", "eval", "failure"]:
            if d.get(k):
                final_log += d[k]
        specs = d.get("specs", {})
        final_log +=(
            f" [ System Specifications ]\n   - OS   : {specs.get('OS', 'Unknown')}\n"
            f"   - CPU  : {specs.get('CPU', 'Unknown')}\n   - RAM  : {specs.get('RAM', 'Unknown')}\n"
            f"   - GPU  : {specs.get('GPU', 'Unknown')}\n\n"
            "======================================================================\n"
        )
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(final_log)
        except Exception:
            pass

    def update_status(self, text):
        if self.is_paused:
            self.last_status = text
        else:
            self.ui_call(self.status_lbl.config, text=text)

    def update_stats(self, percent, fps, avg_fps, eta, curr_time=""):
        text = f"{percent:.1f}% ({curr_time}) | FPS: {fps:05.1f} | Avg: {avg_fps:05.1f} | ETA: {eta}"

        def _update():
            self.progress_var.set(percent)
            self.stats_lbl.config(text=text)

        self.ui_call(_update)

    def safe_set_progress(self, percent=None, text=None):
        def _update():
            if percent is not None:
                self.progress_var.set(percent)
            if text is not None:
                self.stats_lbl.config(text=text)

        self.ui_call(_update)
