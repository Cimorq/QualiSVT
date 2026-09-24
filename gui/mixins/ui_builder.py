"""Tk widget construction, settings save/load and window-level event handlers."""
import json
import multiprocessing
import os
import platform
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

import core.config as config


class UIBuilderMixin:
    def build_ui(self):
        self._build_layout()
        self._build_video_tab()
        self._build_audio_tab()
        self._build_quality_tab()
        self._build_advanced_tab()
        self._build_about_tab()
        self._build_console()

        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_change)
        self.samples_count_var.trace_add("write", lambda *args: self.sync_samples_ui())

        for cb in [
            self.on_engine_change, self.on_encoder_change, self.on_output_mode_change, self.on_op_mode_change,
            self.on_range_mode_change, self.on_eval_submode_change, self.on_audio_codec_change, self.on_gop_mode_change
        ]:
            cb(None)

    def _build_layout(self):
        """Top-level frames, action/progress bar and the file list."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_container = ttk.Frame(main_frame)
        top_container.pack(side=tk.TOP, fill=tk.X)
        bottom_container = ttk.Frame(main_frame)
        bottom_container.pack(side=tk.BOTTOM, fill=tk.X)
        self.middle_container = ttk.Frame(main_frame)
        self.middle_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(5, 5))

        info_frame = ttk.Frame(bottom_container)
        info_frame.pack(side=tk.BOTTOM, fill=tk.X)
        if not config.HAS_DND:
            ttk.Label(
                info_frame,
                text="Tip: Install 'tkinterdnd2' for Drag & Drop support",
                font=("TkDefaultFont", 8, "italic")
            ).pack(side=tk.LEFT, padx=5)
        if not config.HAS_PSUTIL:
            ttk.Label(
                info_frame,
                text="Note: Install 'psutil' to enable Advanced Resource Monitoring and Real-time CPU Limits",
                font=("TkDefaultFont", 8, "italic"),
                foreground="#FF8C00"
            ).pack(side=tk.LEFT, padx=5)

        action_frame = ttk.Frame(bottom_container)
        action_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 5))
        self.btn_start = ttk.Button(
            action_frame, text="Start Encoding", command=self.toggle_encoding, style="Accent.TButton"
        )
        self.btn_start.pack(side=tk.RIGHT, padx=5)
        self.btn_pause = ttk.Button(action_frame, text="Pause", command=self.toggle_pause, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.RIGHT, padx=5)

        self.post_action_frame = ttk.Frame(action_frame)
        self.post_action_frame.pack(side=tk.RIGHT, padx=15)
        ttk.Label(self.post_action_frame, text="Post-Action:").pack(side=tk.LEFT, padx=(0, 2))
        self.power_var = tk.StringVar(value="Do Nothing")
        self.power_cb = ttk.Combobox(
            self.post_action_frame,
            textvariable=self.power_var,
            values=["Do Nothing", "Quit", "Shutdown", "Sleep"],
            state="readonly",
            width=10
        )
        self.power_cb.pack(side=tk.LEFT)

        self.priority_frame = ttk.Frame(action_frame)
        self.priority_frame.pack(side=tk.LEFT, padx=5)
        ttk.Label(self.priority_frame, text="Priority:").pack(side=tk.LEFT, padx=(0, 2))
        self.priority_var = tk.StringVar(value="Normal")
        self.priority_cb = ttk.Combobox(
            self.priority_frame,
            textvariable=self.priority_var,
            values=["Realtime", "High", "Above Normal", "Normal", "Below Normal", "Idle"],
            state="readonly",
            width=12
        )
        self.priority_cb.pack(side=tk.LEFT, padx=(0, 5))
        self.priority_var.trace_add("write", lambda *args: setattr(self, "live_priority", self.priority_var.get()))
        self.priority_cb.bind("<<ComboboxSelected>>", self.apply_process_settings)

        total_cores = multiprocessing.cpu_count() or 1
        core_values = [f"{i}x ({int((i / total_cores) * 100)}%)" for i in range(total_cores, 0, -1)]
        self.cpu_cores_var = tk.StringVar(value=core_values[0])
        self.live_cpu_cores = self.cpu_cores_var.get()
        self.cpu_cores_cb = ttk.Combobox(
            self.priority_frame, textvariable=self.cpu_cores_var, values=core_values, state="readonly", width=12
        )
        self.cpu_cores_cb.pack(side=tk.LEFT, padx=(0, 5))
        self.cpu_cores_var.trace_add("write", lambda *args: setattr(self, "live_cpu_cores", self.cpu_cores_var.get()))
        self.cpu_cores_cb.bind("<<ComboboxSelected>>", self.apply_process_settings)
        if not config.HAS_PSUTIL:
            self.priority_cb.config(state=tk.DISABLED)
            self.cpu_cores_cb.config(state=tk.DISABLED)

        prog_frame = ttk.LabelFrame(bottom_container, text=" Progress ", padding="5")
        prog_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 5))
        self.status_lbl = ttk.Label(prog_frame, text="Status: Idle", font=("TkDefaultFont", 9, "bold"))
        self.status_lbl.pack(anchor=tk.W, padx=5, pady=2)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(prog_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=5, pady=5)
        self.stats_lbl = ttk.Label(prog_frame, text="0% | FPS: 0.0 | Avg: 0.0 | ETA: --")
        self.stats_lbl.pack(anchor=tk.E, padx=5, pady=2)

        self.file_frame = ttk.LabelFrame(top_container, text=" Video Files (Drag & Drop Supported) ", padding="5")
        self.file_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))
        list_container = ttk.Frame(self.file_frame)
        list_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        yscrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL)
        xscrollbar = ttk.Scrollbar(list_container, orient=tk.HORIZONTAL)
        self.listbox = tk.Listbox(
            list_container,
            selectmode=tk.EXTENDED,
            height=4,
            yscrollcommand=yscrollbar.set,
            xscrollcommand=xscrollbar.set
        )
        yscrollbar.config(command=self.listbox.yview)
        xscrollbar.config(command=self.listbox.xview)
        xscrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        yscrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(self.file_frame)
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5)
        ttk.Button(btn_frame, text="Add Files", command=self.add_files).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Add Folder", command=self.add_folder).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Remove Selected", command=self.remove_files).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Clear All", command=self.clear_files).pack(fill=tk.X, pady=2)
        if config.HAS_DND:
            self.listbox.drop_target_register(config.DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self.handle_drop)

        self.notebook = ttk.Notebook(top_container)
        self.notebook.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))

    def _build_video_tab(self):
        """Tab 1: encoder, output and GOP settings."""
        # --- TAB 1: Video Settings ---
        settings_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(settings_frame, text=" Video & Output ")

        enc_lf = ttk.LabelFrame(settings_frame, text=" Encoder Settings ", padding="5")
        enc_lf.pack(fill=tk.X, pady=(0, 5))

        row0_eng = ttk.Frame(enc_lf)
        row0_eng.pack(fill=tk.X, pady=2)
        ttk.Label(row0_eng, text="Final Engine:").pack(side=tk.LEFT, padx=(5, 2))
        self.engine_var = tk.StringVar(value="FFmpeg")
        self.engine_cb = ttk.Combobox(
            row0_eng, textvariable=self.engine_var, values=["FFmpeg", "HandBrakeCLI"], state="readonly", width=15
        )
        self.engine_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.engine_cb.bind("<<ComboboxSelected>>", self.on_engine_change)

        row0 = ttk.Frame(enc_lf)
        row0.pack(fill=tk.X, pady=2)
        ttk.Label(row0, text="Encoder:").pack(side=tk.LEFT, padx=(5, 2))
        self.encoder_var = tk.StringVar(value="SVT-AV1")
        self.encoder_cb = ttk.Combobox(
            row0, textvariable=self.encoder_var, values=["SVT-AV1", "x265 (HEVC)"], state="readonly", width=15
        )
        self.encoder_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.encoder_cb.bind("<<ComboboxSelected>>", self.on_encoder_change)

        ttk.Label(row0, text="Bit-Depth:").pack(side=tk.LEFT, padx=(0, 2))
        self.bit_depth_var = tk.StringVar(value="10-bit (yuv420p10le)")
        self.bit_depth_cb = ttk.Combobox(
            row0,
            textvariable=self.bit_depth_var,
            values=["8-bit (yuv420p)", "10-bit (yuv420p10le)", "12-bit (yuv420p12le)"],
            width=18,
            state="readonly"
        )
        self.bit_depth_cb.pack(side=tk.LEFT, padx=(0, 5))
        self.bit_depth_cb.bind("<<ComboboxSelected>>", lambda e: self.save_settings())

        ttk.Label(row0, text="CRF:").pack(side=tk.LEFT, padx=(5, 2))
        self.crf_var = tk.StringVar(value="34.0")
        self.crf_spinbox = ttk.Spinbox(
            row0, from_=0, to=51, increment=0.5, format="%.1f", textvariable=self.crf_var, width=6
        )
        self.crf_spinbox.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row0, text="Preset:").pack(side=tk.LEFT, padx=(0, 2))
        self.preset_var = tk.StringVar(value="6")
        self.preset_cb = ttk.Combobox(
            row0, textvariable=self.preset_var, values=[str(i) for i in range(-3, 11)], width=10
        )
        self.preset_cb.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row0, text="Tune:").pack(side=tk.LEFT, padx=(0, 2))
        self.tune_var = tk.StringVar(value="0 (vq)")
        self.tune_cb = ttk.Combobox(
            row0,
            textvariable=self.tune_var,
            values=["0 (vq)", "1 (psnr)", "2 (ssim)", "3 (iq)", "4 (ms-ssim)", "5 (grain)"],
            width=12
        )
        self.tune_cb.pack(side=tk.LEFT, padx=(0, 15))

        self.row1_svt = ttk.Frame(enc_lf)
        self.row1_svt.pack(fill=tk.X, pady=2)
        ttk.Label(self.row1_svt, text="SVT Params:").pack(side=tk.LEFT, padx=(5, 2))
        self.svt_params_var = tk.StringVar(value="scd=1:hbd-mds=1:ac-bias=2:enable-variance-boost=1:complex-hvs=1")
        ttk.Entry(self.row1_svt, textvariable=self.svt_params_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5)
        )

        self.row1_x265 = ttk.Frame(enc_lf)
        ttk.Label(self.row1_x265, text="x265 Params:").pack(side=tk.LEFT, padx=(5, 2))
        self.x265_params_var = tk.StringVar(value="crqpoffs=-2:cbqpoffs=-2:aq-mode=3")
        ttk.Entry(self.row1_x265, textvariable=self.x265_params_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5)
        )

        self.row1_gop = ttk.Frame(enc_lf)
        self.row1_gop.pack(fill=tk.X, pady=2)
        ttk.Label(self.row1_gop, text="GOP Mode:").pack(side=tk.LEFT, padx=(5, 2))
        self.gop_mode_var = tk.StringVar(value="10-Second GOP")
        self.gop_mode_cb = ttk.Combobox(
            self.row1_gop,
            textvariable=self.gop_mode_var,
            values=["10-Second GOP", "Encoder Default", "Custom"],
            state="readonly",
            width=16
        )
        self.gop_mode_cb.pack(side=tk.LEFT, padx=(0, 5))
        self.gop_mode_cb.bind("<<ComboboxSelected>>", self.on_gop_mode_change)

        self.gop_custom_sec_var = tk.StringVar(value="10")
        self.gop_custom_sec_entry = ttk.Entry(self.row1_gop, textvariable=self.gop_custom_sec_var, width=5)
        self.gop_custom_sec_entry.pack(side=tk.LEFT, padx=(0, 2))
        self.gop_custom_sec_lbl = ttk.Label(self.row1_gop, text="Seconds")
        self.gop_custom_sec_lbl.pack(side=tk.LEFT, padx=(0, 2))

        out_lf = ttk.LabelFrame(settings_frame, text=" Output & System ", padding="5")
        out_lf.pack(fill=tk.X, pady=5)

        row2 = ttk.Frame(out_lf)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="Output Mode:").pack(side=tk.LEFT, padx=(5, 2))
        self.out_mode_var = tk.StringVar(value="Next to Original")
        self.out_combo = ttk.Combobox(
            row2,
            textvariable=self.out_mode_var,
            values=["Next to Original", "Subfolder", "Browse Folder..."],
            state="readonly",
            width=18
        )
        self.out_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.out_combo.bind("<<ComboboxSelected>>", self.on_output_mode_change)

        self.subfolder_var = tk.StringVar(value="encoded")
        self.subfolder_entry = ttk.Entry(row2, textvariable=self.subfolder_var, width=12)
        self.subfolder_var.trace_add("write", lambda *args: self.update_expected_output_path())

        self.custom_folder_var = tk.StringVar(value="")
        self.custom_folder_frame = ttk.Frame(row2)
        self.custom_folder_entry = ttk.Entry(
            self.custom_folder_frame, textvariable=self.custom_folder_var, width=20, state="readonly"
        )
        self.custom_folder_btn = ttk.Button(self.custom_folder_frame, text="Browse", command=self.browse_output_folder)
        self.custom_folder_entry.pack(side=tk.LEFT, padx=(0, 2))
        self.custom_folder_btn.pack(side=tk.LEFT)

        ttk.Label(row2, text="Format:").pack(side=tk.LEFT, padx=(0, 2))
        self.out_format_var = tk.StringVar(value="MKV")
        self.out_format_cb = ttk.Combobox(
            row2, textvariable=self.out_format_var, values=["MKV", "MP4"], state="readonly", width=8
        )
        self.out_format_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.out_format_cb.bind("<<ComboboxSelected>>", lambda e: self.update_expected_output_path())

        ttk.Label(row2, text="Save Log File:").pack(side=tk.LEFT, padx=(5, 2))
        self.log_loc_var = tk.StringVar(value="Next to Original")
        ttk.Combobox(
            row2,
            textvariable=self.log_loc_var,
            values=["Next to Original", "App 'Logs' Folder", "Don't Save"],
            state="readonly",
            width=18
        ).pack(side=tk.LEFT, padx=(0, 15))

        self.row2_out_path = ttk.Frame(out_lf)
        self.row2_out_path.pack(fill=tk.X, pady=(2, 0), padx=5)
        self.btn_open_output = ttk.Button(
            self.row2_out_path, text="Open Output Folder", command=self.open_output_folder_action, state=tk.DISABLED
        )
        self.btn_open_output.pack(side=tk.LEFT, pady=2)

        row3 = ttk.Frame(out_lf)
        row3.pack(fill=tk.X, pady=2)
        self.auto_resume_var = tk.BooleanVar(value=False)
        self.auto_resume_chk = ttk.Checkbutton(
            row3, text="Enable Auto-Resume (Recover Incomplete Encodes)", variable=self.auto_resume_var
        )
        self.auto_resume_chk.pack(side=tk.LEFT, padx=(5, 15))
        self.auto_resume_var.trace_add("write", lambda *args: self.save_settings())

    def _build_audio_tab(self):
        """Tab 2: audio codec / bitrate / track selection."""
        # --- TAB 2: Audio Settings ---
        audio_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(audio_frame, text=" Audio Settings ")

        audio_lf = ttk.LabelFrame(audio_frame, text=" Audio Tracks & Parameters ", padding="5")
        audio_lf.pack(fill=tk.BOTH, expand=True, pady=5)

        ttk.Label(audio_lf, text="Source Track", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=0, padx=5, pady=5, sticky=tk.W
        )
        ttk.Label(audio_lf, text="Codec", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=1, padx=5, pady=5, sticky=tk.W
        )
        ttk.Label(audio_lf, text="Quality (Bitrate)", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=2, padx=5, pady=5, sticky=tk.W
        )
        ttk.Label(audio_lf, text="Mixdown", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=3, padx=5, pady=5, sticky=tk.W
        )

        self.audio_track_var = tk.StringVar(value="All Audio Tracks")
        ttk.Combobox(
            audio_lf,
            textvariable=self.audio_track_var,
            values=["All Audio Tracks", "Track 1 (Default)"],
            state="readonly",
            width=18
        ).grid(row=1, column=0, padx=5, pady=5)
        self.audio_codec_var = tk.StringVar(value="OPUS")
        self.audio_codec_cb = ttk.Combobox(
            audio_lf, textvariable=self.audio_codec_var, values=["OPUS", "AAC", "Copy"], state="readonly", width=12
        )
        self.audio_codec_cb.grid(row=1, column=1, padx=5, pady=5)
        self.audio_codec_cb.bind("<<ComboboxSelected>>", self.on_audio_codec_change)
        self.audio_br_var = tk.StringVar(value="128K")
        self.audio_br_cb = ttk.Combobox(
            audio_lf,
            textvariable=self.audio_br_var,
            values=["64K", "96K", "128K", "160K", "192K", "256K", "320K", "512K"],
            width=12,
            state="readonly"
        )
        self.audio_br_cb.grid(row=1, column=2, padx=5, pady=5)
        self.audio_mixdown_var = tk.StringVar(value="Auto")
        self.audio_mixdown_cb = ttk.Combobox(
            audio_lf,
            textvariable=self.audio_mixdown_var,
            values=["Auto", "Mono", "Stereo", "5.1", "7.1"],
            state="readonly",
            width=10
        )
        self.audio_mixdown_cb.grid(row=1, column=3, padx=5, pady=5)

    def _build_quality_tab(self):
        """Tab 3: quality estimation, Auto-CRF and metrics."""
        # --- TAB 3: Quality & Metrics ---
        metrics_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(metrics_frame, text=" Quality & Metrics ")
        op_frame = ttk.Frame(metrics_frame)
        op_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(op_frame, text="Operation Mode:").pack(side=tk.LEFT, padx=(5, 2))
        self.op_mode_var = tk.StringVar(value="None (Fastest)")
        self.op_mode_cb = ttk.Combobox(
            op_frame,
            textvariable=self.op_mode_var,
            values=["None (Fastest)", "Enable Quality Estimation", "Enable Auto-CRF Search"],
            state="readonly",
            width=25
        )
        self.op_mode_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.op_mode_cb.bind("<<ComboboxSelected>>", self.on_op_mode_change)

        self.benchmark_var = tk.BooleanVar(value=False)
        self.benchmark_chk = ttk.Checkbutton(
            op_frame,
            text="Analyze Only (Skip Full Encode)",
            variable=self.benchmark_var,
            command=self.on_benchmark_toggle
        )
        self.benchmark_chk.pack(side=tk.LEFT, padx=(5, 5))

        self.samp_lf = ttk.LabelFrame(metrics_frame, text=" Global Sampling Settings ", padding="5")
        self.samp_lf.pack(fill=tk.X, pady=(0, 5))
        row5 = ttk.Frame(self.samp_lf)
        row5.pack(fill=tk.X, pady=2)
        ttk.Label(row5, text="Samples (Count):").pack(side=tk.LEFT, padx=(5, 2))
        self.samples_count_var = tk.StringVar(value="0")
        self.samples_count_entry = ttk.Spinbox(row5, from_=0, to=999, textvariable=self.samples_count_var, width=5)
        self.samples_count_entry.pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(row5, text="(Overrides interval)").pack(side=tk.LEFT, padx=(0, 15))
        self.sample_interval_lbl = ttk.Label(row5, text="Sample-Every (min):")
        self.sample_interval_lbl.pack(side=tk.LEFT, padx=(0, 2))
        self.sample_interval_var = tk.StringVar(value="12")
        self.sample_interval_entry = ttk.Spinbox(row5, from_=1, to=999, textvariable=self.sample_interval_var, width=5)
        self.sample_interval_entry.pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(row5, text="Sample-Duration (s):").pack(side=tk.LEFT, padx=(0, 2))
        self.sample_duration_var = tk.StringVar(value="20")
        self.sample_duration_entry = ttk.Spinbox(row5, from_=1, to=999, textvariable=self.sample_duration_var, width=5)
        self.sample_duration_entry.pack(side=tk.LEFT, padx=(0, 15))

        row6 = ttk.Frame(self.samp_lf)
        row6.pack(fill=tk.X, pady=2)
        ttk.Label(row6, text="Samples Folder:").pack(side=tk.LEFT, padx=(5, 2))
        self.samples_loc_var = tk.StringVar(value="System Temp")
        self.samples_loc_cb = ttk.Combobox(
            row6,
            textvariable=self.samples_loc_var,
            values=["System Temp", "Next to Original"],
            state="readonly",
            width=16
        )
        self.samples_loc_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.keep_samples_var = tk.BooleanVar(value=True)
        self.keep_samples_chk = ttk.Checkbutton(
            row6, text="Keep Samples (Extract Worst/Mid/Best Frames)", variable=self.keep_samples_var
        )
        self.keep_samples_chk.pack(side=tk.LEFT, padx=(0, 15))
        self.use_cache_var = tk.BooleanVar(value=True)
        self.use_cache_chk = ttk.Checkbutton(row6, text="Use Cache (Skip redundant tests)", variable=self.use_cache_var)
        self.use_cache_chk.pack(side=tk.LEFT, padx=(0, 5))

        self.qm_notebook = ttk.Notebook(metrics_frame)
        self.qm_notebook.pack(fill=tk.BOTH, expand=True)
        self.tab_eval = ttk.Frame(self.qm_notebook, padding="5")
        self.tab_autocrf = ttk.Frame(self.qm_notebook, padding="5")
        self.qm_notebook.add(self.tab_eval, text=" Quality Estimation ")
        self.qm_notebook.add(self.tab_autocrf, text=" Auto-CRF Search ")
        self.qm_notebook.bind("<<NotebookTabChanged>>", lambda e: self.adjust_notebook_height())

        qm_lf = ttk.LabelFrame(self.tab_eval, text=" Quality Testing & Metrics ", padding="5")
        qm_lf.pack(fill=tk.X, pady=(0, 5))
        row4 = ttk.Frame(qm_lf)
        row4.pack(fill=tk.X, pady=2)
        ttk.Label(row4, text="Estimation Stage:").pack(side=tk.LEFT, padx=(5, 2))
        self.eval_submode_var = tk.StringVar(value="Both (Pre & Post)")
        self.eval_submode_cb = ttk.Combobox(
            row4,
            textvariable=self.eval_submode_var,
            values=["Pre-Encode Estimate", "Post-Encode Verification", "Both (Pre & Post)"],
            state="readonly",
            width=22
        )
        self.eval_submode_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.eval_submode_cb.bind("<<ComboboxSelected>>", self.on_eval_submode_change)
        self.merge_samples_var = tk.BooleanVar(value=True)
        self.merge_samples_chk = ttk.Checkbutton(
            row4, text="Merge Images (Side-by-Side)", variable=self.merge_samples_var
        )

        row4_1 = ttk.Frame(qm_lf)
        row4_1.pack(fill=tk.X, pady=(5, 2))
        ttk.Label(row4_1, text="Metrics:").pack(side=tk.LEFT, padx=(5, 5))
        self.met_inner_frame = ttk.Frame(row4_1)
        self.met_inner_frame.pack(side=tk.LEFT)
        self.metric_vars = {}
        for m in ["VMAF", "xPSNR", "SSIMULACRA2", "Butteraugli", "CVVDP"]:
            var = tk.BooleanVar(value=(m == "VMAF"))
            self.metric_vars[m] = var
            ttk.Checkbutton(self.met_inner_frame, text=m, variable=var).pack(side=tk.LEFT, padx=3)

        autocrf_lf = ttk.LabelFrame(self.tab_autocrf, text=" Target Quality Preferences ", padding="5")
        autocrf_lf.pack(fill=tk.X, pady=(0, 5))
        row_auto1 = ttk.Frame(autocrf_lf)
        row_auto1.pack(fill=tk.X, pady=5)
        ttk.Label(row_auto1, text="Target Metric:").pack(side=tk.LEFT, padx=(5, 2))
        self.autocrf_metric_var = tk.StringVar(value="VMAF")
        self.autocrf_metric_cb = ttk.Combobox(
            row_auto1,
            textvariable=self.autocrf_metric_var,
            values=["VMAF", "xPSNR", "SSIMULACRA2", "Butteraugli", "CVVDP"],
            state="readonly",
            width=14
        )
        self.autocrf_metric_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.autocrf_metric_cb.bind("<<ComboboxSelected>>", self.on_autocrf_metric_change)
        self.autocrf_score_lbl = ttk.Label(row_auto1, text="Min Target Score:")
        self.autocrf_score_lbl.pack(side=tk.LEFT, padx=(5, 2))
        self.autocrf_score_var = tk.StringVar(value="93.0")
        self.autocrf_score_spinbox = ttk.Spinbox(
            row_auto1, from_=70.0, to=99.0, increment=0.5, format="%.1f", textvariable=self.autocrf_score_var, width=7
        )
        self.autocrf_score_spinbox.pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(row_auto1, text="Max Size Limit (% of Original):").pack(side=tk.LEFT, padx=(5, 2))
        self.autocrf_size_var = tk.StringVar(value="30")
        ttk.Spinbox(
            row_auto1, from_=0, to=100, increment=1, format="%.0f", textvariable=self.autocrf_size_var, width=6
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(row_auto1, text="(0 = No Limit)", font=("TkDefaultFont", 8, "italic")).pack(side=tk.LEFT, padx=(0, 5))
        row_auto2 = ttk.Frame(autocrf_lf)
        row_auto2.pack(fill=tk.X, pady=5)
        ttk.Label(row_auto2, text="Search Range (CRF) - Min:").pack(side=tk.LEFT, padx=(5, 2))
        self.autocrf_min_var = tk.StringVar(value="20.0")
        ttk.Spinbox(
            row_auto2, from_=0, to=51, increment=0.5, format="%.1f", textvariable=self.autocrf_min_var, width=6
        ).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(row_auto2, text="Max:").pack(side=tk.LEFT, padx=(0, 2))
        self.autocrf_max_var = tk.StringVar(value="40.0")
        ttk.Spinbox(
            row_auto2, from_=0, to=51, increment=0.5, format="%.1f", textvariable=self.autocrf_max_var, width=6
        ).pack(side=tk.LEFT, padx=(0, 25))

    def _build_advanced_tab(self):
        """Tab 4: trimming, scaling and custom filters."""
        # --- TAB 4: Advanced & Filters ---
        adv_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(adv_frame, text=" Advanced & Filters ")

        trim_frame = ttk.LabelFrame(adv_frame, text=" Range & Trimming (Applied globally to queue) ", padding="5")
        trim_frame.pack(fill=tk.X, pady=(0, 5))
        row_trim = ttk.Frame(trim_frame)
        row_trim.pack(fill=tk.X, pady=2)
        ttk.Label(row_trim, text="Mode:").pack(side=tk.LEFT, padx=(5, 2))
        self.range_mode_var = tk.StringVar(value="Full Video")
        self.range_combo = ttk.Combobox(
            row_trim,
            textvariable=self.range_mode_var,
            values=["Full Video", "Time (Seconds)", "Time (hh:mm:ss)", "Frames", "Chapters"],
            state="readonly",
            width=16
        )
        self.range_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.range_combo.bind("<<ComboboxSelected>>", self.on_range_mode_change)
        ttk.Label(row_trim, text="Start:").pack(side=tk.LEFT, padx=(0, 2))
        self.range_start_var = tk.StringVar(value="0")
        self.range_start_entry = ttk.Entry(row_trim, textvariable=self.range_start_var, width=10, state=tk.DISABLED)
        self.range_start_entry.pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(row_trim, text="End:").pack(side=tk.LEFT, padx=(0, 2))
        self.range_end_var = tk.StringVar(value="0")
        self.range_end_entry = ttk.Entry(row_trim, textvariable=self.range_end_var, width=10, state=tk.DISABLED)
        self.range_end_entry.pack(side=tk.LEFT, padx=(0, 15))
        self.range_hint = ttk.Label(row_trim, text="", font=("TkDefaultFont", 8, "italic"))
        self.range_hint.pack(side=tk.LEFT, padx=(0, 5))

        qf_frame = ttk.LabelFrame(adv_frame, text=" Quick Video Filters ", padding="5")
        qf_frame.pack(fill=tk.X, pady=5)
        row7 = ttk.Frame(qf_frame)
        row7.pack(fill=tk.X, pady=2)
        ttk.Label(row7, text="Resize/Scale:").pack(side=tk.LEFT, padx=(5, 2))
        self.scale_var = tk.StringVar(value="Original")
        ttk.Combobox(
            row7,
            textvariable=self.scale_var,
            values=["Original", "4K (2160p)", "1440p", "1080p", "720p", "480p"],
            state="readonly",
            width=14
        ).pack(side=tk.LEFT, padx=(0, 15))
        self.auto_crop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row7, text="Auto Crop Black Bars", variable=self.auto_crop_var).pack(side=tk.LEFT, padx=(0, 15))
        self.deint_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row7, text="Deinterlace (yadif)", variable=self.deint_var).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(row7, text="Auto Fade:").pack(side=tk.LEFT, padx=(0, 2))
        self.fade_mode_var = tk.StringVar(value="None")
        self.fade_mode_cb = ttk.Combobox(
            row7,
            textvariable=self.fade_mode_var,
            values=["None", "Fade In", "Fade Out", "Both"],
            state="readonly",
            width=8
        )
        self.fade_mode_cb.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(row7, text="Dur (s):").pack(side=tk.LEFT, padx=(0, 2))
        self.fade_dur_var = tk.StringVar(value="1.5")
        self.fade_dur_entry = ttk.Entry(row7, textvariable=self.fade_dur_var, width=5)
        self.fade_dur_entry.pack(side=tk.LEFT, padx=(0, 5))

        self.custom_cmd_container = ttk.Frame(adv_frame)
        self.custom_cmd_container.pack(fill=tk.X, pady=5)

        self.cf_ffmpeg_frame = ttk.LabelFrame(self.custom_cmd_container, text=" Custom FFmpeg Filters ", padding="5")
        row8 = ttk.Frame(self.cf_ffmpeg_frame)
        row8.pack(fill=tk.X, pady=2)
        ttk.Label(row8, text="Video (-vf):").pack(side=tk.LEFT, padx=(5, 2))
        self.custom_vf_var = tk.StringVar(value="")
        ttk.Entry(row8, textvariable=self.custom_vf_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Label(row8, text="e.g. hqdn3d=1.5:1.5:6:6", font=("TkDefaultFont", 8, "italic")).pack(
            side=tk.LEFT, padx=(0, 5)
        )
        row9 = ttk.Frame(self.cf_ffmpeg_frame)
        row9.pack(fill=tk.X, pady=2)
        ttk.Label(row9, text="Audio (-af):").pack(side=tk.LEFT, padx=(5, 2))
        self.custom_af_var = tk.StringVar(value="")
        ttk.Entry(row9, textvariable=self.custom_af_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Label(row9, text="e.g. volume=1.5", font=("TkDefaultFont", 8, "italic")).pack(side=tk.LEFT, padx=(0, 42))

        self.cf_hb_frame = ttk.LabelFrame(self.custom_cmd_container, text=" Custom HandBrake Params ", padding="5")
        row10 = ttk.Frame(self.cf_hb_frame)
        row10.pack(fill=tk.X, pady=2)
        ttk.Label(row10, text="Extra Args:").pack(side=tk.LEFT, padx=(5, 2))
        self.custom_hb_var = tk.StringVar(value="")
        ttk.Entry(row10, textvariable=self.custom_hb_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Label(row10, text="e.g. --rotate 4", font=("TkDefaultFont", 8, "italic")).pack(side=tk.LEFT, padx=(0, 5))

    def _build_about_tab(self):
        """Tab 5: about / credits."""
        # --- TAB 5: About ---
        about_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(about_frame, text=" About ")
        about_lf = ttk.LabelFrame(about_frame, text=" Software Information ", padding="15")
        about_lf.pack(fill=tk.BOTH, expand=True, pady=5)
        ttk.Label(
            about_lf,
            text=(
                f"QualiSVT v{config.APP_VERSION}\nAn advanced batch video encoder and quality assessment tool.\n"
                "Specifically designed and optimized for custom SVT-AV1 forks."
            ),
            wraplength=900,
            justify=tk.LEFT,
            font=("TkDefaultFont", 10)
        ).pack(anchor=tk.W, pady=(0, 15))
        ttk.Label(
            about_lf, text="Author: Simorq (assisted by Google AI Studio)", font=("TkDefaultFont", 9, "bold")
        ).pack(anchor=tk.W, pady=(0, 5))

        def make_link(parent, text, url):
            lbl = ttk.Label(
                parent, text=text, foreground="#87CEEB", cursor="hand2", font=("TkDefaultFont", 9, "underline")
            )
            lbl.pack(anchor=tk.W, padx=15, pady=2)
            lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open_new_tab(u))

        make_link(about_lf, "🔗 QualiSVT GitHub Repository", "https://github.com/Cimorq/QualiSVT")
        ttk.Label(about_lf, text="Powered by these amazing open-source tools:", font=("TkDefaultFont", 9, "bold")).pack(
            anchor=tk.W, pady=(15, 5)
        )
        make_link(
            about_lf,
            "• FFmpeg-Builds-SVT-AV1-HDR",
            "https://github.com/QuickFatHedgehog/FFmpeg-Builds-SVT-AV1-HDR/releases/tag/latest"
        )
        make_link(
            about_lf,
            "• HandBrakeCLI (SVT-AV1-Tritium)",
            "https://github.com/Uranite/HandBrake-SVT-AV1-Tritium/releases/tag/win"
        )
        make_link(about_lf, "• Vship (FFVship)", "https://codeberg.org/Line-fr/Vship")

    def _build_console(self):
        """Log console with colour tags and its context menu."""
        self.console_frame = ttk.LabelFrame(self.middle_container, text=" Logs & Output ", padding="5")
        self.console_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.console = scrolledtext.ScrolledText(
            self.console_frame, bg="black", fg="white", font=("Consolas", 9), state=tk.DISABLED, height=12
        )
        self.console.pack(fill=tk.BOTH, expand=True)
        for tag, color in [
            ("info", "#FFFFFF"),
            ("header", "#00FFFF"),
            ("success", "#32CD32"),
            ("error", "#FF4500"),
            ("warning", "#FFD700"),
            ("q_vlossless", "#00FFFF"),
            ("q_super", "#00FF7F"),
            ("q_high", "#9ACD32"),
            ("q_med", "#FFD700"),
            ("q_low", "#FF8C00"),
            ("q_bad", "#FF4500"),
            ("svt_cfg", "#87CEEB")
        ]:
            self.console.tag_config(tag, foreground=color, font=("Consolas", 9, "bold") if tag == "header" else None)

        self.console_menu = tk.Menu(self.console, tearoff=0)
        self.console_menu.add_command(
            label="Copy", command=lambda: self.root.clipboard_append(self.console.selection_get())
        )
        self.console_menu.add_separator()
        self.console_menu.add_command(
            label="Select All",
            command=lambda: [self.console.tag_add(tk.SEL, "1.0", tk.END), self.console.see(tk.INSERT)]
        )
        self.console.bind(
            "<Button-2>" if platform.system() == "Darwin" else "<Button-3>",
            lambda e: self.console_menu.tk_popup(e.x_root, e.y_root)
        )

    def on_gop_mode_change(self, event=None):
        if hasattr(self, "gop_mode_var"):
            st = tk.NORMAL if self.gop_mode_var.get() == "Custom" else tk.DISABLED
            self.gop_custom_sec_lbl.config(state=st)
            self.gop_custom_sec_entry.config(state=st)

    def on_audio_codec_change(self, event=None):
        if hasattr(self, "audio_codec_var"):
            st = tk.DISABLED if self.audio_codec_var.get() == "Copy" else "readonly"
            self.audio_br_cb.config(state=st)
            self.audio_mixdown_cb.config(state=st)

    def adjust_notebook_height(self):
        try:
            self.root.update_idletasks()
            current_tab_id = self.notebook.select()
            if current_tab_id:
                self.notebook.config(height=self.notebook.nametowidget(current_tab_id).winfo_reqheight())
        except Exception:
            pass

    def on_tab_change(self, event=None):
        try:
            if self.notebook.tab(self.notebook.select(), "text").strip() == "About":
                self.middle_container.pack_forget()
            else:
                self.middle_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(5, 5))
            self.adjust_notebook_height()
        except Exception:
            pass

    def set_ui_state(self, disable=True):
        """Disable/restore the UI without losing each widget's pre-encode state."""

        def _toggle(widget):
            try:
                if disable:
                    # Capture the current state only once per disable cycle. This
                    # prevents repeated disable calls from overwriting the real
                    # pre-encode state with "disabled".
                    if not hasattr(widget, "_quali_original_state"):
                        widget._quali_original_state = widget.cget("state")
                    widget.configure(state=tk.DISABLED)
                else:
                    original_state = getattr(widget, "_quali_original_state", tk.NORMAL)
                    widget.configure(state=original_state)
                    if hasattr(widget, "_quali_original_state"):
                        delattr(widget, "_quali_original_state")
            except (tk.TclError, AttributeError):
                pass
            try:
                for child in widget.winfo_children():
                    _toggle(child)
            except tk.TclError:
                pass

        for f in [self.file_frame, self.notebook]:
            _toggle(f)
        self.power_cb.configure(state="readonly")

        if not disable:
            for cb in [self.on_engine_change, self.on_encoder_change, self.on_output_mode_change,
                       self.on_op_mode_change, self.on_range_mode_change, self.on_audio_codec_change,
                       self.on_gop_mode_change]:
                cb(None)
            self.sync_samples_ui()
            self.on_benchmark_toggle()

    def sync_samples_ui(self):
        if self.op_mode_var.get() == "None (Fastest)":
            self.sample_interval_entry.config(state=tk.DISABLED)
            self.sample_interval_lbl.config(state=tk.DISABLED)
            return
        try:
            count = int(self.samples_count_var.get())
        except ValueError:
            count = 0
        st = tk.DISABLED if count > 0 else tk.NORMAL
        self.sample_interval_entry.config(state=st)
        self.sample_interval_lbl.config(state=st)

    def on_eval_submode_change(self, event=None):
        mode = getattr(self, "eval_submode_var", tk.StringVar()).get()
        if hasattr(self, "merge_samples_chk"):
            if (
                mode in ["Post-Encode Verification", "Both (Pre & Post)"]
                and self.op_mode_var.get() == "Enable Quality Estimation"
            ):
                self.merge_samples_chk.pack(side=tk.LEFT, padx=(15, 5))
            else:
                self.merge_samples_chk.pack_forget()
        self.adjust_notebook_height()

    def on_op_mode_change(self, event=None):
        mode = self.op_mode_var.get()

        def _toggle(widget, disable=True):
            try:
                widget.configure(state=tk.DISABLED if disable else tk.NORMAL)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                _toggle(child, disable)

        if mode == "None (Fastest)":
            self.benchmark_var.set(False)
            self.benchmark_chk.config(state=tk.DISABLED)
            for f in [self.samp_lf, self.tab_eval, self.tab_autocrf]:
                _toggle(f, True)
        elif mode == "Enable Quality Estimation":
            self.benchmark_chk.config(state=tk.NORMAL)
            _toggle(self.samp_lf, False)
            _toggle(self.tab_eval, False)
            _toggle(self.tab_autocrf, True)
            self.qm_notebook.select(0)
            if self.benchmark_var.get():
                self.eval_submode_var.set("Pre-Encode Estimate")
                self.eval_submode_cb.config(state=tk.DISABLED)
            else:
                self.eval_submode_cb.config(state="readonly")
        elif mode == "Enable Auto-CRF Search":
            self.benchmark_chk.config(state=tk.NORMAL)
            _toggle(self.samp_lf, False)
            _toggle(self.tab_eval, True)
            _toggle(self.tab_autocrf, False)
            self.qm_notebook.select(1)

        # [FIX] Unconditionally call on_benchmark_toggle to re-enable output fields when switching to None
        self.on_benchmark_toggle()
        self.sync_samples_ui()
        self.on_eval_submode_change()
        self.adjust_notebook_height()

    def on_benchmark_toggle(self, event=None):
        is_bench = self.benchmark_var.get()
        cb_st = tk.DISABLED if is_bench else "readonly"
        self.out_combo.config(state=cb_st)
        self.out_format_cb.config(state=cb_st)
        if hasattr(self, "custom_folder_btn"):
            self.custom_folder_btn.config(state=tk.DISABLED if is_bench else tk.NORMAL)
        if hasattr(self, "subfolder_entry"):
            self.subfolder_entry.config(state=tk.DISABLED if is_bench else tk.NORMAL)
        if self.op_mode_var.get() == "Enable Quality Estimation":
            if is_bench:
                self.eval_submode_var.set("Pre-Encode Estimate")
                self.eval_submode_cb.config(state=tk.DISABLED)
            else:
                self.eval_submode_cb.config(state="readonly")

    def on_autocrf_metric_change(self, event=None):
        metric = self.autocrf_metric_var.get().strip().upper()
        cfgs = {
            "VMAF": ("Min", 70.0, 99.0, 0.5, "93.0"),
            "SSIMULACRA2": ("Min", 60.0, 99.0, 0.5, "65.0"),
            "BUTTERAUGLI": ("Max", 0.4, 4.0, 0.1, "1.5"),
            "CVVDP": ("Min", 8.0, 9.9, 0.1, "9.5"),
            "XPSNR": ("Min", 30.0, 45.0, 0.5, "38.0")
        }
        if metric in cfgs:
            lbl, f, t, inc, default = cfgs[metric]
            self.autocrf_score_lbl.config(text=f"{lbl} Target Score:")
            self.autocrf_score_spinbox.config(from_=f, to=t, increment=inc, format="%.1f" if inc==0.5 else "%.2f")
            # [FIX] When called without an event (e.g. during load_settings), validate the
            # currently stored score against the new metric's range and clamp/reset if invalid.
            if event:
                self.autocrf_score_var.set(default)
            else:
                try:
                    cur = float(self.autocrf_score_var.get())
                    if cur < f or cur > t:
                        self.autocrf_score_var.set(default)
                except (ValueError, TypeError):
                    self.autocrf_score_var.set(default)

    def on_engine_change(self, event=None):
        if self.engine_var.get() == "HandBrakeCLI":
            self.fade_mode_cb.config(state=tk.DISABLED)
            self.fade_dur_entry.config(state=tk.DISABLED)
            self.cf_ffmpeg_frame.pack_forget()
            self.cf_hb_frame.pack(fill=tk.X)
        else:
            self.fade_mode_cb.config(state="readonly")
            self.fade_dur_entry.config(state=tk.NORMAL)
            self.cf_hb_frame.pack_forget()
            self.cf_ffmpeg_frame.pack(fill=tk.X)
        self.on_encoder_change(None)

    def on_encoder_change(self, event=None):
        enc = self.encoder_var.get()
        self.crf_spinbox.config(from_=0, to=51, increment=0.5, format="%.1f")
        if hasattr(self, "bit_depth_cb"):
            self.bit_depth_cb.config(
                values=["8-bit (yuv420p)", "10-bit (yuv420p10le)"]
                if (self.engine_var.get() == "HandBrakeCLI" and enc == "SVT-AV1")
                else ["8-bit (yuv420p)", "10-bit (yuv420p10le)", "12-bit (yuv420p12le)"]
            )
            if "12-bit" in self.bit_depth_var.get() and self.engine_var.get() == "HandBrakeCLI" and enc == "SVT-AV1":
                self.bit_depth_var.set("10-bit (yuv420p10le)")
        if enc == "SVT-AV1":
            # [FIX] Use the same upper bound as the Combobox values (-3..10) so validation
            # of a loaded preset is consistent with what the user can actually select.
            preset_values = [str(i) for i in range(-3, 11)]
            self.preset_cb.config(values=preset_values)
            try:
                if not (-3 <= int(self.preset_var.get()) <= 10):
                    self.preset_var.set("6")
            except Exception:
                self.preset_var.set("6")
            self.tune_cb.config(values=["0 (vq)", "1 (psnr)", "2 (ssim)", "3 (iq)", "4 (ms-ssim)", "5 (grain)"])
            if self.tune_var.get() not in self.tune_cb["values"]:
                self.tune_var.set(
                    {
                        "0": "0 (vq)",
                        "1": "1 (psnr)",
                        "2": "2 (ssim)",
                        "3": "3 (iq)",
                        "4": "4 (ms-ssim)",
                        "5": "5 (grain)",
                        "vq": "0 (vq)"
                    }.get(self.tune_var.get(), "0 (vq)")
                )
            self.row1_x265.pack_forget()
            self.row1_svt.pack(fill=tk.X, pady=2)
        else:
            self.preset_cb.config(
                values=[
                    "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow",
                    "placebo"
                ]
            )
            if self.preset_var.get().lstrip("-").isdigit():
                self.preset_var.set("medium")
            self.tune_cb.config(
                values=["None", "film", "animation", "grain", "stillimage", "psnr", "ssim", "fastdecode", "zerolatency"]
            )
            if self.tune_var.get() not in self.tune_cb["values"]:
                self.tune_var.set("None")
            self.row1_svt.pack_forget()
            self.row1_x265.pack(fill=tk.X, pady=2)
        self.adjust_notebook_height()

    def get_current_settings_dict(self):
        return {
            "engine": self.engine_var.get(),
            "encoder": self.encoder_var.get(),
            "crf": self.crf_var.get(),
            "tune": self.tune_var.get(),
            "preset": self.preset_var.get(),
            "bit_depth": self.bit_depth_var.get(),
            "gop_mode": getattr(self, "gop_mode_var", tk.StringVar(value="10-Second GOP")).get(),
            "gop_custom_sec": getattr(self, "gop_custom_sec_var", tk.StringVar(value="10")).get(),
            "audio_track": self.audio_track_var.get(),
            "audio_codec": self.audio_codec_var.get(),
            "audio_br": self.audio_br_var.get(),
            "audio_mixdown": self.audio_mixdown_var.get(),
            "svt_params": self.svt_params_var.get(),
            "x265_params": self.x265_params_var.get(),
            "out_mode": self.out_mode_var.get(),
            "subfolder": self.subfolder_var.get(),
            "custom_folder": getattr(self, "custom_folder_var", tk.StringVar()).get(),
            "out_format": self.out_format_var.get(),
            "power": self.power_var.get(),
            "log_loc": self.log_loc_var.get(),
            "auto_resume": getattr(self, "auto_resume_var", tk.BooleanVar(value=False)).get(),
            "files_queue": self.files_to_process if getattr(
                self, "auto_resume_var", tk.BooleanVar(value=False)
            ).get() else [],
            "op_mode": self.op_mode_var.get(),
            "eval_submode": self.eval_submode_var.get(),
            "benchmark": self.benchmark_var.get(),
            "metrics": {m: v.get() for m, v in self.metric_vars.items()},
            "samples_count": self.samples_count_var.get(),
            "sample_dur": self.sample_duration_var.get(),
            "sample_int": self.sample_interval_var.get(),
            "samples_loc": self.samples_loc_var.get(),
            "keep_samples": self.keep_samples_var.get(),
            "merge_samples": getattr(self, "merge_samples_var", tk.BooleanVar(value=True)).get(),
            "use_cache": self.use_cache_var.get(),
            "autocrf_metric": self.autocrf_metric_var.get(),
            "autocrf_score": self.autocrf_score_var.get(),
            "autocrf_min": self.autocrf_min_var.get(),
            "autocrf_max": self.autocrf_max_var.get(),
            "autocrf_size": self.autocrf_size_var.get(),
            "range_mode": self.range_mode_var.get(),
            "range_start": self.range_start_var.get(),
            "range_end": self.range_end_var.get(),
            "scale": self.scale_var.get(),
            "auto_crop": self.auto_crop_var.get(),
            "deint": self.deint_var.get(),
            "fade_mode": self.fade_mode_var.get(),
            "fade_dur": self.fade_dur_var.get(),
            "custom_vf": self.custom_vf_var.get(),
            "custom_af": self.custom_af_var.get(),
            "custom_hb": self.custom_hb_var.get(),
            "priority": self.priority_var.get(),
            "cpu_cores": self.cpu_cores_var.get()
        }

    def save_settings(self):
        try:
            os.makedirs(os.path.dirname(config.CONFIG_FILE), exist_ok=True)
            with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.get_current_settings_dict(), f, indent=4)
        except Exception as e:
            print(f"Save config error: {e}")

    def load_settings(self):
        if not config.CONFIG_FILE or not os.path.exists(config.CONFIG_FILE):
            return
        try:
            with open(config.CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in [
                "engine", "encoder", "crf", "preset", "bit_depth", "gop_mode", "gop_custom_sec", "audio_track",
                "audio_codec", "audio_br", "audio_mixdown", "svt_params", "x265_params", "out_mode", "subfolder",
                "custom_folder", "out_format", "power", "log_loc", "auto_resume", "op_mode", "eval_submode",
                "benchmark", "samples_count", "sample_dur", "sample_int", "samples_loc", "keep_samples",
                "merge_samples", "use_cache", "autocrf_metric", "autocrf_score", "autocrf_min", "autocrf_max",
                "autocrf_size", "range_mode", "range_start", "range_end", "scale", "auto_crop", "deint", "fade_mode",
                "fade_dur", "custom_vf", "custom_af", "custom_hb", "priority", "cpu_cores"
            ]:
                if k in saved and hasattr(self, f"{k}_var"):
                    getattr(self, f"{k}_var").set(saved[k])

            if "tune" in saved:
                self.tune_var.set(
                    {
                        "0": "0 (vq)",
                        "1": "1 (psnr)",
                        "2": "2 (ssim)",
                        "3": "3 (iq)",
                        "4": "4 (ms-ssim)",
                        "5": "5 (grain)"
                    }.get(saved["tune"], saved["tune"])
                    if saved["tune"].isdigit()
                    else saved["tune"]
                )
            if saved.get("auto_resume") and "files_queue" in saved:
                for f_path in saved["files_queue"]:
                    if os.path.exists(f_path) and f_path not in self.files_to_process:
                        self.files_to_process.append(f_path)
                        self.listbox.insert(tk.END, f_path)
            if "metrics" in saved:
                for m, v in saved["metrics"].items():
                    if m in self.metric_vars:
                        self.metric_vars[m].set(v)
            self.on_autocrf_metric_change(None)
            for cb in [
                self.on_engine_change, self.on_encoder_change, self.on_output_mode_change, self.on_op_mode_change,
                self.on_range_mode_change, self.on_eval_submode_change, self.on_audio_codec_change,
                self.on_gop_mode_change
            ]:
                cb(None)
            self.sync_samples_ui()
            self.update_expected_output_path()
            self.adjust_notebook_height()
        except Exception as e:
            print(f"Load config error: {e}")

    def on_closing(self):
        self.save_settings()
        if (
            self.is_encoding
            and not messagebox.askyesno(
                "Confirm Exit", "An encoding process is currently running.\n\nDo you want to stop it and exit?"
            )
        ):
            return
        self.stop_encoding(wait=True)  # stops the monitor and kills the whole child tree before the app disappears
        self._ui_closed = True
        try:
            self.root.after_cancel(self._drain_after_id)
        except Exception:
            pass
        self.root.destroy()

    def browse_output_folder(self):
        if folder := filedialog.askdirectory(title="Select Output Folder"):
            self.custom_folder_var.set(folder)
            self.update_expected_output_path()

    def open_output_folder_action(self):
        if not self.files_to_process:
            return
        file_dir = os.path.split(self.files_to_process[0])[0]
        mode = getattr(self, "out_mode_var", tk.StringVar(value="Next to Original")).get()
        target_dir = (
            os.path.join(
                file_dir, getattr(self, "subfolder_var", tk.StringVar(value="encoded")).get().strip() or "encoded"
            )
            if mode == "Subfolder"
            else (
                getattr(self, "custom_folder_var", tk.StringVar(value="")).get().strip()
                or file_dir
                if mode == "Browse Folder..."
                else file_dir
            )
        )
        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception:
                return
        if platform.system() == "Windows":
            os.startfile(target_dir)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", target_dir])
        else:
            subprocess.Popen(["xdg-open", target_dir])

    def update_expected_output_path(self, event=None):
        if hasattr(self, "btn_open_output"):
            self.btn_open_output.config(state=tk.NORMAL if self.files_to_process else tk.DISABLED)

    def on_output_mode_change(self, event):
        mode = self.out_mode_var.get()
        self.subfolder_entry.pack_forget()
        self.custom_folder_frame.pack_forget()
        if mode == "Subfolder":
            self.subfolder_entry.pack(side=tk.LEFT, padx=(0, 15), after=self.out_combo)
        elif mode == "Browse Folder...":
            self.custom_folder_frame.pack(side=tk.LEFT, padx=(0, 15), after=self.out_combo)
        self.update_expected_output_path()
        self.adjust_notebook_height()

    def on_range_mode_change(self, event):
        mode = self.range_mode_var.get()
        self.range_start_entry.config(state=tk.NORMAL if mode != "Full Video" else tk.DISABLED)
        self.range_end_entry.config(state=tk.NORMAL if mode != "Full Video" else tk.DISABLED)
        hints = {
            "Time (hh:mm:ss)": "e.g. 00:01:30 to 00:05:00",
            "Time (Seconds)": "e.g. 90.5 to 300.0",
            "Frames": "e.g. 1500 to 4000",
            "Chapters": "e.g. 1 to 3"
        }
        self.range_hint.config(text=hints.get(mode, ""))
