"""Adding, removing and clearing input files (buttons and drag & drop)."""
import os
import re
import tkinter as tk
from tkinter import filedialog

import core.config as config


class FileListMixin:
    def _add_files_core(self, files):
        for f in files:
            if f.lower().endswith(config.VIDEO_EXTENSIONS) and f not in self.files_to_process:
                self.files_to_process.append(f)
                self.listbox.insert(tk.END, f)
        self.update_expected_output_path()
        self.save_settings()

    def handle_drop(self, event):
        self._add_files_core(re.findall(r"\{(.*?)\}", event.data) if "{" in event.data else event.data.split())

    def add_files(self):
        self._add_files_core(
            filedialog.askopenfilenames(
                title="Select Videos", filetypes=[("Video Files", "*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.webm *.m4v")]
            )
        )

    def add_folder(self):
        if folder := filedialog.askdirectory(title="Select Folder"):
            for root_dir, _, files in os.walk(folder):
                self._add_files_core([os.path.join(root_dir, f) for f in files])

    def remove_files(self):
        for idx in reversed(list(self.listbox.curselection())):
            self.listbox.delete(idx)
            del self.files_to_process[idx]
        self.update_expected_output_path()
        self.save_settings()

    def clear_files(self):
        self.listbox.delete(0, tk.END)
        self.files_to_process.clear()
        self.update_expected_output_path()
        self.save_settings()
