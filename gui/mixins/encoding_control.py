"""Start / stop / pause / resume buttons and the state they drive."""
import os
import shutil
import threading
import time
import tkinter as tk
from tkinter import messagebox

import core.config as config
from core.proc_utils import kill_process_tree, pause_subprocess, resume_subprocess


class EncodingControlMixin:
    def get_adjusted_elapsed_time(self, start_time):
        return max(
            0,
            time.time() - start_time - (
                self.current_paused_duration + (time.time() - self.pause_start_time if self.is_paused else 0)
            )
        )

    def toggle_encoding(self):
        self.stop_encoding() if self.is_encoding else self.start_encoding()

    def toggle_pause(self):
        if not self.is_encoding:
            return
        with self._proc_lock:
            if self.is_paused:
                for pid in list(self._suspended):
                    resume_subprocess(self._suspended.pop(pid))
                self.is_paused = False
            else:
                proc = self.current_process
                if proc is not None and proc.poll() is None:
                    self._suspended[proc.pid] = pause_subprocess(proc.pid)  # whole tree, not just the top PID
                self.pause_start_time = time.time()
                self.is_paused = True  # a step that starts later is frozen by _register_process()
        if self.is_paused:
            self.btn_pause.config(text="Resume")
            self.last_status = self.status_lbl.cget("text")
            self.status_lbl.config(text="Status: Paused")
            self.log("[!] Process paused.", tag="warning")
        else:
            self.current_paused_duration = self.current_paused_duration + (time.time() - self.pause_start_time)
            self.btn_pause.config(text="Pause")
            self.status_lbl.config(text=getattr(self, "last_status", "Status: Resumed"))
            self.log("[+] Process resumed.", tag="success")

    def start_encoding(self):
        if not self.files_to_process:
            return messagebox.showwarning("No Files", "Please add video files first.")
        if (
            self.engine_var.get() == "HandBrakeCLI"
            and not shutil.which(config.HANDBRAKE_EXE)
            and not os.path.isfile(config.HANDBRAKE_EXE)
        ):
            return messagebox.showerror(
                "Missing Dependency",
                "HandBrakeCLI was not found in your system PATH or app directory.\n\n"
                "Please install it or switch the Engine back to FFmpeg."
            )
        self.save_settings()
        self.current_job_settings = self.get_current_settings_dict()
        # snapshot: the worker must not iterate a list the UI can still change
        self.job_files = list(self.files_to_process)
        self.is_encoding, self.is_paused, self.cancel_requested, self.current_paused_duration = True, False, False, 0.0
        self._suspended.clear()
        self.btn_start.config(text="Stop / Cancel")
        self.btn_pause.config(state=tk.NORMAL, text="Pause")
        self.set_ui_state(disable=True)
        self.console.config(state=tk.NORMAL)
        self.console.delete(1.0, tk.END)
        self.console.config(state=tk.DISABLED)
        threading.Thread(target=self.process_queue, daemon=True).start()

    def stop_encoding(self, wait=False):
        monitor = self.active_monitor  # the worker thread owns clearing the attribute (see _cleanup_job_resources)
        if monitor:
            monitor.stop()
        if not self.is_encoding:
            return
        self.cancel_requested = True
        self.log("\n[!] Cancellation requested. Stopping current process...", tag="error")
        if self.is_paused:
            self.toggle_pause()  # unfreeze first so the state/UI are consistent
        with self._proc_lock:
            proc = self.current_process
        if proc is not None:
            # Kills the process AND its children. Off the UI thread unless we are about to exit.
            if wait:
                kill_process_tree(proc)
            else:
                threading.Thread(target=kill_process_tree, args=(proc,), daemon=True).start()
