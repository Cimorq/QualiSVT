"""The main per-file encode loop; uses every other mixin."""
import datetime
import os
import re
import subprocess
import time
import traceback
import tkinter as tk

import core.config as config
from core.fs_utils import silent_remove
from core.output_capture import OutputCapture
from core.proc_utils import kill_process_tree
from core.video_info import (
    format_duration, get_chapter_times, get_duration, get_fps, get_total_frames, parse_time, shorten_filename
)
from core.vmaf_utils import get_quality_label
from plugins.monitor_ohm import OpenHardwareMonitorPlugin, get_system_specs


class QueueRunnerMixin:
    def process_queue(self):
        reports = []
        total_start_time = time.time()
        s = self.current_job_settings
        engine = self.current_job_settings.get("engine", "FFmpeg")
        files = list(self.job_files)

        try:
            for index, input_file in enumerate(files, start=1):
                if self.cancel_requested:
                    break

                try:
                    # never attribute a failure to the previous file
                    self.current_log_data, self.current_log_file = {}, None
                    is_benchmark, op_mode = s.get("benchmark", False), s.get("op_mode", "None (Fastest)")
                    file_dir, file_name = os.path.split(input_file)
                    name_we = shorten_filename(os.path.splitext(file_name)[0], max_len=100)

                    out_fmt_pref = s.get("out_format", "MKV")
                    target_ext = ".mp4" if out_fmt_pref == "MP4" else ".mkv"
                    mode = s.get("out_mode", "Next to Original")
                    target_dir = (
                        os.path.join(file_dir, s.get("subfolder", "encoded").strip() or "encoded")
                        if mode == "Subfolder"
                        else (
                            s.get("custom_folder", "").strip() or file_dir if mode == "Browse Folder..." else file_dir
                        )
                    )
                    os.makedirs(target_dir, exist_ok=True)
                    output_file = os.path.join(target_dir, f"{name_we}_encoded{target_ext}")

                    timestamp = datetime.datetime.now().strftime("_%d.%m.%Y %H-%M-%S")
                    start_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    log_loc_pref = s.get("log_loc", "Next to Original")
                    log_file = None
                    if log_loc_pref == "App 'Logs' Folder":
                        os.makedirs(os.path.join(config.APP_DIR, "Logs"), exist_ok=True)
                        log_file = os.path.join(config.APP_DIR, "Logs", f"{name_we}_encoded{timestamp}.txt")
                    elif log_loc_pref == "Next to Original":
                        log_file = os.path.join(file_dir, f"{name_we}_encoded{timestamp}.txt")
                    self.current_log_file = log_file

                    self.log(f"\n==================================================", tag="header")
                    self.log(
                        f"[{index}/{len(files)}] {'ANALYZE ONLY' if is_benchmark else 'Processing'}: {file_name}",
                        tag="header"
                    )
                    self.update_status(f"Status: Processing {file_name} ({index}/{len(files)})")

                    total_duration = get_duration(input_file)
                    if total_duration == 0.0:
                        self.log(" -> Skipping: Could not read video duration.", tag="warning")
                        continue
                    input_size = os.path.getsize(input_file)
                    self.current_gop_frames = self.calculate_gop(input_file, total_duration)

                    self.current_crop = None
                    if s.get("auto_crop", False):
                        self.update_status(f"Status: Detecting crop bounds for {file_name}...")
                        if crop_val := self.detect_crop(input_file, total_duration):
                            self.current_crop = crop_val
                            self.log(f" -> Auto Crop detected: {crop_val}", tag="info")
                        else:
                            self.log(f" -> Auto Crop enabled but no black bars detected.", tag="warning")

                    r_mode, seek_start, encode_duration = s.get("range_mode", "Full Video"), 0.0, total_duration
                    start_val = s.get("range_start", "0").strip() or "0"
                    end_val = s.get("range_end", "0").strip() or "0"
                    if r_mode != "Full Video":
                        try:
                            if r_mode == "Time (Seconds)":
                                seek_start, encode_duration = float(start_val), float(end_val) - float(start_val)
                            elif r_mode == "Time (hh:mm:ss)":
                                seek_start = parse_time(start_val)
                                encode_duration = parse_time(end_val) - parse_time(start_val)
                            elif r_mode == "Frames":
                                vid_fps_tmp = get_fps(input_file)
                                seek_start = int(start_val) / vid_fps_tmp
                                encode_duration = (int(end_val) - int(start_val)) / vid_fps_tmp
                            elif r_mode == "Chapters":
                                c_start, c_end = get_chapter_times(input_file, int(start_val), int(end_val))
                                if c_start is not None:
                                    seek_start, encode_duration = c_start, c_end - c_start
                                else:
                                    self.log(" -> No chapters found. Encoding full video.", tag="warning")
                            seek_start, encode_duration = max(0.0, min(seek_start, total_duration)), min(
                                encode_duration, total_duration - seek_start
                            ) if encode_duration > 0 else total_duration - seek_start
                        except Exception as e:
                            self.log(f" -> Range parse error ({e}). Falling back to full video.", tag="error")
                            seek_start, encode_duration = 0.0, total_duration

                    self.current_log_data = {
                        "file": file_name,
                        "start": start_time_str,
                        "end": "Running...",
                        "specs": get_system_specs(),
                        "orig_specs": self.get_original_file_specs(input_file),
                        "enc": "",
                        "eval": "",
                        "settings": "",
                        "failure": ""
                    }
                    self.save_current_log(log_file)

                    final_crf, qual_scores_pre, est_size, est_time = None, {}, 0.0, 0.0

                    if is_benchmark and config.HAS_PSUTIL:
                        self.active_monitor = OpenHardwareMonitorPlugin(self)  # PLUGIN HOOK: system monitor
                        self.active_monitor.start()

                    self.current_log_data["settings"] = self.extract_and_log_encoder_info(
                        input_file,
                        file_name,
                        out_fmt_pref,
                        r_mode,
                        seek_start,
                        encode_duration,
                        crf_override="Auto (Search)" if op_mode == "Enable Auto-CRF Search" else None
                    )
                    if self.cancel_requested:
                        break
                    self.save_current_log(log_file)

                    if op_mode == "Enable Auto-CRF Search":
                        res = self.run_auto_crf_search(
                            input_file, encode_duration, file_dir, name_we, seek_offset=seek_start
                        )
                        if self.cancel_requested:
                            break
                        final_crf, qual_scores_pre, est_size, est_time, is_auto_crf_success = res if len(
                            res
                        ) == 5 else (*res, True)

                        if not is_auto_crf_success:
                            self.log(f" -> Skipping {file_name}: Auto-CRF constraints not met.", tag="error")
                            self.current_log_data["eval"] = f" [ Estimation Results ]\n   --- Auto-CRF (Predicted) ---\n   - Status                  : Failed (Constraints Unmet)\n   - Selected CRF            : {final_crf if final_crf is not None else 'None'}\n   - Predicted Output Size   : {est_size / (1024*1024):.2f} MB\n   - Predicted Encode Time   : {format_duration(est_time)}\n" + "".join(
                                [
                                    f"   - Pred. Quality ({m}): {s_val:.2f} [{get_quality_label(m, s_val)}]\n" if s_val is not None else f"   - Pred. Quality ({m}): N/A [Unknown]\n" for m,
                                    s_val in qual_scores_pre.items()
                                ]
                            ) + "\n"
                            self.current_log_data["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self.save_current_log(log_file)
                            if final_crf is not None:
                                self.ui_call(self.crf_var.set, str(final_crf))
                            reports.append(
                                {
                                    "name": f"{file_name} (Auto-CRF Failed)",
                                    "in_size": input_size,
                                    "out_size": est_size,
                                    "duration": est_time,
                                    "fps": 0,
                                    "ratio": (est_size / input_size) * 100 if input_size > 0 else 0,
                                    "reduction": 100 - ((est_size / input_size) * 100 if input_size > 0 else 0),
                                    "qual_pre": qual_scores_pre,
                                    "qual_post": {}
                                }
                            )
                            # the per-file finally block below stops the ProcessMonitor and any leftover child process
                            continue
                        else:
                            self.current_log_data["eval"] = f" [ Estimation Results ]\n   --- Auto-CRF (Predicted) ---\n   - Status                  : Success\n   - Selected CRF            : {final_crf:g}\n   - Predicted Output Size   : {est_size / (1024*1024):.2f} MB\n   - Predicted Encode Time   : {format_duration(est_time)}\n" + "".join(
                                [
                                    f"   - Pred. Quality ({m}): {s_val:.2f} [{get_quality_label(m, s_val)}]\n" if s_val is not None else f"   - Pred. Quality ({m}): N/A [Unknown]\n" for m,
                                    s_val in qual_scores_pre.items()
                                ]
                            ) + "\n"
                            self.save_current_log(log_file)
                            self.ui_call(self.crf_var.set, str(final_crf))

                    elif op_mode == "Enable Quality Estimation":
                        if (
                            s.get("eval_submode", "Both (Pre & Post)") in ["Pre-Encode Estimate", "Both (Pre & Post)"]
                            or is_benchmark
                        ):
                            qual_scores_pre, est_size, est_time = self.run_pre_quality_test(
                                input_file,
                                encode_duration,
                                file_dir,
                                name_we,
                                seek_offset=seek_start,
                                crf_override=final_crf
                            )
                            if self.cancel_requested:
                                break
                            if qual_scores_pre:
                                self.current_log_data["eval"] = f" [ Estimation Results ]\n   --- Pre-Encode (Predicted) ---\n   - Predicted Output Size   : {est_size / (1024*1024):.2f} MB\n   - Predicted Encode Time   : {format_duration(est_time)}\n" + "".join(
                                    [
                                        f"   - Pred. Quality ({m}): {s_val:.2f} [{get_quality_label(m, s_val)}]\n" if s_val is not None else f"   - Pred. Quality ({m}): N/A [Unknown]\n" for m,
                                        s_val in qual_scores_pre.items()
                                    ]
                                ) + "\n"
                                self.save_current_log(log_file)

                    if is_benchmark:
                        # [FIX] Generate the resource report BEFORE stopping/clearing the monitor.
                        # Previously the monitor was stopped and set to None first, making the
                        # subsequent "if self.active_monitor" check always fail — so CPU/RAM/Power
                        # stats were never reported in Analyze-Only mode.
                        self.log(f"\n -> Analyze Only Finished for {file_name}", tag="success")
                        est_ratio = (est_size / input_size) * 100 if input_size > 0 else 0
                        if getattr(self, "active_monitor", None):
                            rep_actual = self.active_monitor.get_report()
                            rep_proj = self.active_monitor.get_report(est_time / 3600.0)
                            self.log(
                                f"   - Peak RAM Usage : {rep_actual['peak_ram']:.0f} MB\n"
                                f"   - Avg CPU Usage  : {rep_actual['avg_cpu']:.1f} %",
                                tag="info"
                            )
                            bench_res = (
                                " [ Projected Resource Usage (Full Encode) ]\n"
                                f"   - Peak RAM Usage : {rep_actual['peak_ram']:.0f} MB\n"
                                f"   - Avg CPU Usage  : {rep_actual['avg_cpu']:.1f} %\n"
                            )
                            if rep_actual["avg_power"] > 0:
                                self.log(
                                    f"   - Avg CPU Power  : {rep_actual['avg_power']:.1f} W\n"
                                    f"   - Est. Total Engy: {rep_proj['total_wh']:.3f} Wh",
                                    tag="info"
                                )
                                bench_res +=(
                                    f"   - Avg CPU Power  : {rep_actual['avg_power']:.1f} W\n"
                                    f"   - Est. Total Engy: {rep_proj['total_wh']:.3f} Wh\n"
                                )
                            else:
                                self.log("   - Avg CPU Power  : Not Detected (Is OHM running?)", tag="warning")
                                bench_res += "   - Avg CPU Power  : Not Detected (OHM not running)\n"
                            self.current_log_data["eval"] = self.current_log_data.get("eval", "") + bench_res + "\n"
                            self.active_monitor.stop()
                            self.active_monitor = None

                        reports.append(
                            {
                                "name": f"{file_name} (Analyze Only)",
                                "in_size": input_size,
                                "out_size": est_size,
                                "duration": est_time,
                                "fps": 0,
                                "ratio": est_ratio,
                                "reduction": 100 - est_ratio,
                                "qual_pre": qual_scores_pre,
                                "qual_post": {}
                            }
                        )
                        self.current_log_data["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.save_current_log(log_file)
                        continue

                    apply_trim = r_mode != "Full Video" and encode_duration < total_duration
                    is_resuming = False
                    skip_encode = False
                    incomplete_file = output_file + ".incomplete"
                    part1_file = output_file + ".001.incomplete"
                    part2_file = output_file + ".002.incomplete"
                    cmd_seek_start, cmd_encode_duration = seek_start, encode_duration

                    if s.get("auto_resume", False):
                        if os.path.exists(part1_file) and os.path.exists(part2_file):
                            self.update_status(f"Status: Merging previous parts for {file_name}...")
                            self.log(
                                " -> Found previously interrupted resumed parts. Merging them first...", tag="warning"
                            )
                            if self.concat_files(part1_file, part2_file, output_file + ".merge.incomplete"):
                                silent_remove(part1_file)
                                silent_remove(part2_file)
                                try:
                                    os.rename(output_file + ".merge.incomplete", part1_file)
                                except Exception:
                                    pass
                            else:
                                self.log(" -> Failed to merge previous parts. Dropping the broken part2.", tag="error")
                                silent_remove(part2_file)

                        if (
                            os.path.exists(output_file)
                            and not os.path.exists(incomplete_file)
                            and not os.path.exists(part1_file)
                        ):
                            if 0 < self.get_actual_readable_duration(output_file) < encode_duration - 3:
                                self.log(
                                    " -> Found legacy incomplete output file. Renaming to .incomplete...", tag="warning"
                                )
                                try:
                                    os.rename(output_file, incomplete_file)
                                except Exception:
                                    pass

                        if os.path.exists(incomplete_file) and not os.path.exists(part1_file):
                            try:
                                os.rename(incomplete_file, part1_file)
                            except Exception:
                                pass

                        if os.path.exists(part1_file):
                            if os.path.getsize(part1_file) > 0:
                                actual_duration = self.get_actual_readable_duration(part1_file)
                                if 0 < actual_duration < encode_duration - 3:
                                    self.log(
                                        " -> Incomplete output detected. Readable duration: "
                                        f"{format_duration(actual_duration)}. Resuming...",
                                        tag="warning"
                                    )
                                    is_resuming = True
                                    cmd_seek_start = seek_start + actual_duration
                                    cmd_encode_duration = encode_duration - actual_duration
                                    apply_trim = True
                                    target_output_file = part2_file
                                elif actual_duration >= encode_duration - 3:
                                    self.log(
                                        " -> Output file seems complete. Restoring and skipping encode.", tag="success"
                                    )
                                    try:
                                        os.rename(part1_file, output_file)
                                    except Exception:
                                        pass
                                    skip_encode = True
                                else:
                                    self.log(
                                        " -> Incomplete file unreadable or zero duration. Starting from scratch.",
                                        tag="error"
                                    )
                                    silent_remove(part1_file)
                            else:
                                silent_remove(part1_file)

                    target_output_file = incomplete_file if not skip_encode and not is_resuming else (
                        part2_file if is_resuming else output_file
                    )

                    if not skip_encode:
                        cmd = (
                            self.get_handbrake_cmd(
                                input_file,
                                target_output_file,
                                encode_duration=cmd_encode_duration,
                                seek_start=cmd_seek_start,
                                apply_trim=apply_trim,
                                crf_override=final_crf
                            )
                            if engine== "HandBrakeCLI"
                            else self.get_ffmpeg_cmd(
                                input_file,
                                target_output_file,
                                target_duration=cmd_encode_duration,
                                is_sample=False,
                                with_audio=True,
                                seek_start=cmd_seek_start,
                                apply_trim=apply_trim,
                                crf_override=final_crf
                            )
                        )

                        self.update_status(f"Status: Encoding {file_name} [{engine}]")
                        self.update_stats(0.0, 0.0, 0.0, "Calc...")
                        self.current_paused_duration, start_time = 0.0, time.time()

                        proc = self._spawn_tracked(
                            cmd,
                            stdout=subprocess.PIPE if engine == "HandBrakeCLI" else subprocess.DEVNULL,
                            stderr=subprocess.STDOUT if engine == "HandBrakeCLI" else subprocess.PIPE,
                            text=True, errors="replace", universal_newlines=True
                        )
                        # everything the encoder prints, so a failure can be logged completely
                        capture = OutputCapture()

                        if config.HAS_PSUTIL:
                            self.active_monitor = OpenHardwareMonitorPlugin(self)  # PLUGIN HOOK: system monitor
                            self.active_monitor.start()

                        # [FIX] Loosened time pattern for very long videos.
                        time_pattern = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
                        fps_pattern = re.compile(r"fps=\s*([\d\.]+)")
                        frame_pattern = re.compile(r"frame=\s*(\d+)")
                        hb_pattern = re.compile(
                            r"(\d+(?:\.\d+)?)\s*%\s*\(([\d\.]+)\s*fps,\s*avg\s*([\d\.]+)\s*fps,\s*ETA\s*([^)]+)\)"
                        )

                        while True:
                            if self.cancel_requested:
                                kill_process_tree(proc)
                                break

                            line = proc.stdout.readline() if engine == "HandBrakeCLI" else proc.stderr.readline()
                            if not line:
                                if proc.poll() is not None:
                                    break
                                time.sleep(0.05)  # EOF but not reaped yet: don't spin
                                continue
                            capture.add(line)

                            if engine == "HandBrakeCLI":
                                if match_hb := hb_pattern.search(line):
                                    progress = float(match_hb.group(1))
                                    cur_fps = float(match_hb.group(2))
                                    avg_fps = float(match_hb.group(3))
                                    eta_str = match_hb.group(4)
                                    self.update_stats(progress, cur_fps, avg_fps, eta_str, "")
                            else:
                                if match_time := time_pattern.search(line):
                                    cur_fps = float(fps_pattern.search(line).group(1)) if fps_pattern.search(
                                        line
                                    ) else 0.0
                                    cur_frame = int(frame_pattern.search(line).group(1)) if frame_pattern.search(
                                        line
                                    ) else 0
                                    cur_sec = parse_time(match_time.group(1))

                                    progress = min((cur_sec / cmd_encode_duration) * 100, 100.0)
                                    elapsed = self.get_adjusted_elapsed_time(start_time)
                                    avg_fps = (cur_frame / elapsed) if elapsed > 0 else 0.0
                                    eta_str = format_duration(
                                        max(0, ((elapsed / cur_sec) * cmd_encode_duration) - elapsed)
                                    ) if cur_sec > 0 else "Calc..."
                                    self.update_stats(progress, cur_fps, avg_fps, eta_str, match_time.group(1))

                        proc.wait()
                        if getattr(self, "active_monitor", None):
                            self.active_monitor.stop()

                        if self.cancel_requested:
                            break
                        if proc.returncode != 0:
                            self.log(f" -> Failed: {engine} exited with error code {proc.returncode}", tag="error")
                            self._record_failure(
                                f"Encode ({engine})", cmd, proc.returncode, capture.render(), console_tail=60
                            )
                            self.current_log_data["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self.save_current_log(log_file)
                            continue

                        frames_encoded = get_total_frames(
                            part2_file if is_resuming and os.path.exists(part2_file) else (
                                target_output_file if os.path.exists(target_output_file) else ""
                            ),
                            run=self._run_tracked
                        )

                        if is_resuming and os.path.exists(part2_file) and os.path.exists(part1_file):
                            if self.concat_files(part1_file, part2_file, output_file):
                                self.log(" -> Resumed parts successfully concatenated.", tag="success")
                                silent_remove(part1_file)
                                silent_remove(part2_file)
                            else:
                                self.log(
                                    " -> Failed to concatenate resumed parts. Final file is incomplete/broken.",
                                    tag="error"
                                )

                        else:
                            if os.path.exists(target_output_file) and target_output_file.endswith(".incomplete"):
                                try:
                                    os.rename(target_output_file, output_file)
                                except Exception as e:
                                    self.log(f" -> Failed to rename incomplete file: {e}", tag="error")

                        encoding_duration = self.get_adjusted_elapsed_time(start_time)
                    else:
                        encoding_duration, frames_encoded = 0.0, 0
                        self.update_stats(100.0, 0.0, 0.0, "0s", "Skipped")

                    output_size = os.path.getsize(output_file) if os.path.exists(output_file) else 0
                    final_avg_fps = frames_encoded / encoding_duration if encoding_duration > 0 else 0
                    size_ratio = (output_size / input_size) * 100 if input_size > 0 else 0
                    compression_ratio = 100 - size_ratio

                    self.log(
                        "\n"
                        f" -> Encoding Completed in {format_duration(encoding_duration)} | Space Saved: "
                        f"{compression_ratio:.1f}%",
                        tag="success"
                    )

                    enc_log = (
                        f" [ Encoding Results ]\n   - Original Size  : {input_size / (1024*1024):.2f} MB\n"
                        f"   - Encoded Size   : {output_size / (1024*1024):.2f} MB\n"
                        f"   - Storage Saved  : {compression_ratio:.1f}% reduction\n"
                        f"   - Avg Speed      : {final_avg_fps:.1f} FPS | Time: {format_duration(encoding_duration)}\n"
                    )
                    if not skip_encode and getattr(self, "active_monitor", None):
                        rep = self.active_monitor.get_report(encoding_duration / 3600.0)
                        self.log(
                            f"   - Peak RAM Usage : {rep['peak_ram']:.0f} MB\n"
                            f"   - Avg CPU Usage  : {rep['avg_cpu']:.1f} %",
                            tag="info"
                        )
                        enc_log +=(
                            f"   - Peak RAM Usage : {rep['peak_ram']:.0f} MB\n"
                            f"   - Avg CPU Usage  : {rep['avg_cpu']:.1f} %\n"
                        )
                        if rep["avg_power"] > 0:
                            self.log(
                                f"   - Avg CPU Power  : {rep['avg_power']:.1f} W\n"
                                f"   - Energy Consumed: {rep['total_wh']:.3f} Wh",
                                tag="info"
                            )
                            enc_log +=(
                                f"   - Avg CPU Power  : {rep['avg_power']:.1f} W\n"
                                f"   - Total Energy   : {rep['total_wh']:.3f} Wh\n"
                            )
                        else:
                            self.log("   - Avg CPU Power  : Not Detected (Is OHM running?)", tag="warning")
                            enc_log += "   - Avg CPU Power  : Not Detected (OHM not running)\n"

                    self.current_log_data["enc"] = enc_log + "\n"
                    self.save_current_log(log_file)
                    self.active_monitor = None

                    qual_scores_post = {}
                    if (
                        op_mode == "Enable Quality Estimation"
                        and s.get("eval_submode", "Both (Pre & Post)") in [
                            "Post-Encode Verification", "Both (Pre & Post)"
                        ]
                    ):
                        qual_scores_post = self.run_post_quality_test(
                            input_file, output_file, encode_duration, file_dir, name_we, seek_offset=seek_start
                        )
                        if self.cancel_requested:
                            break
                        if qual_scores_post:
                            post_log = "   --- Post-Encode (Actual) ---\n" + "".join(
                                [
                                    f"   - Actual Quality({m}): {s_val:.2f} [{get_quality_label(m, s_val)}]\n" if s_val is not None else f"   - Actual Quality({m}): N/A [Unknown]\n" for m,
                                    s_val in qual_scores_post.items()
                                ]
                            ) + "\n"
                            self.current_log_data["eval"] = self.current_log_data.get(
                                "eval", " [ Estimation Results ]\n"
                            ) + post_log
                            self.save_current_log(log_file)

                    self.current_log_data["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.save_current_log(log_file)

                    reports.append(
                        {
                            "name": file_name, "in_size": input_size, "out_size": output_size,
                            "duration": encoding_duration, "fps": final_avg_fps, "ratio": size_ratio,
                            "reduction": compression_ratio, "qual_pre": qual_scores_pre, "qual_post": qual_scores_post
                        }
                    )
                finally:
                    # monitor + child processes are released on every exit path: break, continue, error
                    self._cleanup_job_resources()

            if self.cancel_requested:
                self.log("\n[!] Process was cancelled by user.", tag="error")
                self.update_status("Status: Cancelled")
            else:
                self.log("\n==================================================", tag="header")
                self.log(
                    "             ANALYZE ONLY SUMMARY           " if s.get(
                        "benchmark", False
                    ) else "              ENCODING SUMMARY              ",
                    tag="header"
                )
                self.log("==================================================", tag="header")

                total_in, total_out = sum(r["in_size"] for r in reports), sum(r["out_size"] for r in reports)
                for rep in reports:
                    qual_txt = "".join(
                        [
                            f" | Est. {m}: {s_val:.2f} [{get_quality_label(m, s_val)}]" if s_val is not None else f" | Est. {m}: N/A" for m,
                            s_val in rep["qual_pre"].items()
                        ]
                    ) + "".join([f" | Act. {m}: {s_val:.2f} [{get_quality_label(m, s_val)}]" if s_val is not None else f" | Act. {m}: N/A" for m, s_val in rep["qual_post"].items()])
                    size_str = f"-> {rep['out_size']/(1024*1024):.1f}MB" if rep[
                        "out_size"
                    ] > 0 else "(Analyze Only or Failed)"
                    self.log(
                        f" - {rep['name']}: {rep['in_size']/(1024*1024):.1f}MB {size_str} "
                        f"({rep['ratio']:.1f}%){qual_txt}",
                        tag="success"
                    )

                if reports:
                    self.log(f"\n Total Processed : {len(reports)}")
                    self.log(f" Total Time      : {format_duration(time.time() - total_start_time)}")
                    if total_in > 0 and total_out > 0:
                        self.log(
                            f" Total Saved     : {100 - ((total_out / total_in) * 100):.2f}% reduction", tag="success"
                        )

                self.update_status("Status: All Tasks Completed!")

                p_action = s.get("power", "Do Nothing")
                if p_action == "Quit":
                    self.log("Closing application in 3 seconds...")
                    self.ui_call(lambda: self.root.after(3000, self.root.destroy))
                elif p_action == "Shutdown":
                    self.log("Shutting down in 10 seconds...", tag="warning")
                    os.system("shutdown /s /t 10")
                elif p_action == "Sleep":
                    self.log("Putting system to sleep...", tag="info")
                    os.system(
                        "rundll32.exe powrprof.dll,SetSuspendState 0,1,0" if os.name == "nt" else "systemctl suspend"
                    )

        except Exception as e:
            tb = traceback.format_exc()
            self.log(f"\n[!] A critical error occurred in the queue: {e}", tag="error")
            self.log(tb, tag="error")
            traceback.print_exc()
            try:  # keep the full traceback in the job's log file too
                if isinstance(self.current_log_data, dict) and self.current_log_data:
                    self.current_log_data["failure"] = self.current_log_data.get(
                        "failure", ""
                    ) + f" [ FAILURE: Unhandled exception in queue ]\n{tb}\n"
                    self.current_log_data["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.save_current_log(self.current_log_file)
            except Exception:
                pass
        finally:
            self._cleanup_job_resources()

            def _reset_gui():
                with self._proc_lock:
                    self.is_paused = False
                self.is_encoding = False
                self.btn_start.config(state=tk.NORMAL, text="Start Encoding")
                self.btn_pause.config(state=tk.DISABLED, text="Pause")
                self.set_ui_state(disable=False)

            self.ui_call(_reset_gui)
