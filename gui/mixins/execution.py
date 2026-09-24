"""Runs a single command and streams live progress into the UI."""
import re
import subprocess
import time

from core.output_capture import OutputCapture
from core.video_info import parse_time


class ExecutionMixin:
    def run_command_with_progress(self, cmd, actual_sample_duration, cwd, progress_label, engine="FFmpeg"):
        is_hb = engine == "HandBrakeCLI"
        proc = self._spawn_tracked(
            cmd,
            stdout=subprocess.PIPE if is_hb else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if is_hb else subprocess.PIPE,
            text=True,
            errors="replace",
            universal_newlines=True,
            cwd=cwd
        )
        stream, capture = (proc.stdout if is_hb else proc.stderr), OutputCapture()

        # [FIX] Loosened pattern (any digit count for hours).
        time_pattern = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
        hb_pattern = re.compile(r"(\d+(?:\.\d+)?)\s*%")
        filter_error = False

        try:
            while True:
                line = stream.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)  # EOF but not reaped yet: don't spin
                    continue
                capture.add(line)
                if "No such filter" in line:
                    filter_error = True

                if is_hb:
                    if match_hb := hb_pattern.search(line):
                        prog = float(match_hb.group(1))
                        self.safe_set_progress(prog, f"{progress_label}... {prog:.1f}%")
                else:
                    if match_time := time_pattern.search(line):
                        prog = min((parse_time(match_time.group(1)) / actual_sample_duration) * 100, 100.0)
                        self.safe_set_progress(prog, f"{progress_label}... {prog:.1f}%")

            proc.wait()
            if self.cancel_requested:
                raise Exception("Cancelled")
            ok = (not filter_error) and (proc.returncode == 0)
            if not ok:
                self._record_failure(progress_label, cmd, proc.returncode, capture.render(), console_tail=15)
            return ok
        finally:
            self._release_process(proc)  # also covers unexpected exceptions: never leave an orphaned encoder behind
