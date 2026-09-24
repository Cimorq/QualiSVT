"""FFVship/VMAF scoring, Auto-CRF search and pre/post-encode quality tests."""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time

import core.config as config
from core.cache_db import check_cache, save_cache, tool_fingerprint
from core.fs_utils import silent_remove
from core.video_info import format_duration, format_fps, get_duration, get_fps, get_video_dimensions, get_video_pix_fmt, get_video_stream_size
from core.vmaf_utils import get_metric_thread_count, get_quality_color_tag, get_quality_label, parse_vmaf_json_score


class QualityEvalMixin:
    def run_ffvship_metric(self, source, encoded, metric, work_dir, status_prefix="Calculating"):
        json_path = os.path.join(work_dir, f".ffvship_{metric.lower()}_{os.getpid()}_{int(time.time()*1000000)}.json")
        proc = self._spawn_tracked(
            [config.FFVSHIP_EXE, "-s", source, "-e", encoded, "-m", metric, "--json", json_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            universal_newlines=True,
            cwd=work_dir
        )
        output_lines = []

        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)  # EOF but not reaped yet: don't spin
                    continue
                output_lines.append(line.rstrip())
                if self.cancel_requested:
                    raise Exception("Cancelled")  # the finally block kills FFVship and any children it spawned

            proc.wait()
            if self.cancel_requested:
                raise Exception("Cancelled")
            output, frame_scores = "\n".join(output_lines), []

            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as jf:
                        data = json.load(jf)
                    values = next(
                        (data[key] for key in ("scores", "frames", "data", "results") if key in data), data
                    ) if isinstance(data, dict) else data
                    if isinstance(values, list):
                        for idx, item in enumerate(values):
                            val = None
                            if isinstance(item, (int, float)):
                                val = float(item)
                            elif isinstance(item, list) and item:
                                try:
                                    val = float(item[0])
                                except Exception:
                                    pass
                            elif isinstance(item, dict):
                                for key in ("score", "value", "cvvdp", "ssimulacra2", "butteraugli"):
                                    if key in item:
                                        try:
                                            val = float(item[key])
                                            break
                                        except Exception:
                                            pass
                            if val is not None:
                                frame_scores.append((idx, val))
                except Exception as e:
                    self.log(f" ├─ FFVship JSON parse warning ({metric}): {e}", tag="warning")

            final_score, metric_upper = None, metric.upper()
            if metric_upper == "CVVDP":
                m = re.search(r"Video Score:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", output, re.I)
                final_score = float(m.group(1)) if m else (frame_scores[-1][1] if frame_scores else None)
            elif metric_upper == "SSIMULACRA2":
                m = re.search(r"Average\s*:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", output, re.I)
                final_score = float(m.group(1)) if m else (
                    sum(v for _, v in frame_scores) / len(frame_scores) if frame_scores else None
                )
            elif metric_upper == "BUTTERAUGLI":
                in_2norm = False
                for line in output.splitlines():
                    if re.search(r"2-Norm", line, re.I):
                        in_2norm = True
                        continue
                    if in_2norm:
                        if m := re.search(r"Average\s*:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", line, re.I):
                            final_score = float(m.group(1))
                            break
                if final_score is None and frame_scores:
                    final_score = sum(v for _, v in frame_scores) / len(frame_scores)

            if proc.returncode != 0 and final_score is None:
                self._record_failure(
                    f"FFVship ({metric})",
                    [config.FFVSHIP_EXE, "-s", source, "-e", encoded, "-m", metric, "--json", json_path],
                    proc.returncode,
                    "\n".join(output_lines[-3000:]),
                    console_tail=15
                )
                raise RuntimeError(f"FFVship exited with code {proc.returncode}")
            return final_score, frame_scores, output
        finally:
            self._release_process(proc)
            silent_remove(json_path)

    def evaluate_metric_ffmpeg(
        self,
        m_name,
        enc_sample,
        ref_sample,
        actual_sample_duration,
        fps,
        ref_pix_fmt,
        ref_filter_str,
        work_dir,
        s_idx,
        s_tot,
        enc_w,
        enc_h,
        progress_label
    ):
        log_filename = f"sample_{s_idx}_{m_name.lower()}.log"
        log_filepath = os.path.join(work_dir, log_filename)
        silent_remove(log_filepath)

        if m_name == "xPSNR":
            main_scale = ""
            if enc_w and enc_h:
                ref_w, ref_h = get_video_dimensions(ref_sample)
                if ref_w and ref_h and (enc_w != ref_w or enc_h != ref_h):
                    main_scale = f",scale={ref_w}:{ref_h}:flags=bicubic"
            filter_str = f"[0:v]settb=AVTB,setpts=PTS-STARTPTS{main_scale},format={ref_pix_fmt}[main];[1:v]{ref_filter_str}settb=AVTB,setpts=PTS-STARTPTS[ref];[ref][main]xpsnr=eof_action=endall:stats_file='{log_filename}'"
        else:
            main_scale = ",scale=1920:1080:flags=bicubic" if (enc_w != 1920 or enc_h != 1080) else ""
            ref_w, ref_h = get_video_dimensions(ref_sample)
            ref_scale = ",scale=1920:1080:flags=bicubic" if (ref_w != 1920 or ref_h != 1080) else ""
            filter_str = f"[0:v]settb=AVTB,setpts=PTS-STARTPTS{main_scale},format={ref_pix_fmt}[main];[1:v]{ref_filter_str}settb=AVTB,setpts=PTS-STARTPTS{ref_scale}[ref];[main][ref]libvmaf=eof_action=endall:log_fmt=json:shortest=true:ts_sync_mode=nearest:log_path='{log_filename}':n_threads={get_metric_thread_count()}:pool=Mean:model=version=vmaf_v0.6.1"

        qual_cmd = [
            config.FFMPEG_EXE,
            "-hide_banner",
            "-nostdin",
            "-probesize",
            "50M",
            "-r",
            format_fps(fps),
            "-i",
            enc_sample,
            "-r",
            format_fps(fps),
            "-i",
            ref_sample,
            "-lavfi",
            filter_str,
            "-f",
            "null",
            "-"
        ]
        filter_ok = self.run_command_with_progress(
            qual_cmd, actual_sample_duration, work_dir, progress_label, engine="FFmpeg"
        )

        qual_score, frame_scores = None, []
        if filter_ok and os.path.exists(log_filepath):
            try:
                if m_name == "xPSNR":
                    with open(log_filepath, "r", encoding="utf-8") as f:
                        for line in f:
                            if match := re.search(
                                r"n:\s*(\d+)\s+XPSNR\s+y:\s*([\d\.]+).*?u:\s*([\d\.]+).*?v:\s*([\d\.]+)",
                                line,
                                re.IGNORECASE
                            ):
                                frame_scores.append(
                                    (
                                        int(match.group(1)),
                                        (
                                            (4.0 * float(match.group(2))) + float(match.group(3)) + float(
                                                match.group(4)
                                            )
                                        ) / 6.0
                                    )
                                )
                            elif match_avg := re.search(r"XPSNR\s+average.*?y:\s*([\d\.]+).*?u:\s*([\d\.]+).*?v:\s*([\d\.]+)", line, re.IGNORECASE):
                                qual_score = (
                                    (4.0 * float(match_avg.group(1))) + float(match_avg.group(2)) + float(
                                        match_avg.group(3)
                                    )
                                ) / 6.0
                else:
                    with open(log_filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if (pooled := parse_vmaf_json_score(data)) is not None:
                        qual_score = pooled
                    for frame in (data.get("frames", []) if isinstance(data, dict) else []):
                        if (
                            val := (frame.get("metrics", {}) if isinstance(frame, dict) else {}).get("vmaf")
                        ) is not None:
                            frame_scores.append((frame.get("frameNum", len(frame_scores)), float(val)))
                if qual_score is None and frame_scores:
                    qual_score = sum(s for _, s in frame_scores) / len(frame_scores)
            except Exception:
                filter_ok = False
        else:
            filter_ok = False

        silent_remove(log_filepath)
        return qual_score, frame_scores, not filter_ok

    def get_metric_score(
        self,
        m_name,
        enc_sample,
        ref_sample,
        actual_sample_duration,
        fps,
        ref_pix_fmt,
        ref_filter_str,
        work_dir,
        s_idx,
        s_tot,
        enc_w,
        enc_h,
        progress_label=None
    ):
        if not progress_label:
            progress_label = f"Calculating {m_name} {s_idx}/{s_tot}"
        if m_name in ["SSIMULACRA2", "Butteraugli", "CVVDP"]:
            try:
                qual_score, frame_scores, _ = self.run_ffvship_metric(
                    ref_sample, enc_sample, m_name, work_dir, status_prefix=progress_label
                )
                self.safe_set_progress(None, f"{progress_label}... {len(frame_scores)} frames")
                filter_error = qual_score is None
            except Exception:
                if self.cancel_requested:
                    raise
                qual_score, frame_scores, filter_error = None, [], True
        else:
            qual_score, frame_scores, filter_error = self.evaluate_metric_ffmpeg(
                m_name,
                enc_sample,
                ref_sample,
                actual_sample_duration,
                fps,
                ref_pix_fmt,
                ref_filter_str,
                work_dir,
                s_idx,
                s_tot,
                enc_w,
                enc_h,
                progress_label
            )
        return qual_score, frame_scores, filter_error

    def get_sampling_settings(self, target_duration):
        s = self.current_job_settings
        try:
            s_dur = float(str(s.get("sample_dur", "20")).strip())
        except Exception:
            s_dur = 20.0
        try:
            s_count = int(str(s.get("samples_count", "0")).strip())
        except Exception:
            s_count = 0
        try:
            s_interval = float(str(s.get("sample_int", "12")).strip()) * 60
        except Exception:
            s_interval = 12 * 60

        if target_duration <= 0:
            return s_dur, [0.0]
        num_samples = s_count if s_count > 0 else max(
            1, math.ceil(target_duration / (s_interval if s_interval > 0 else 12 * 60))
        )
        if num_samples * s_dur >= target_duration:
            if s_dur >= target_duration:
                return target_duration, [0.0]
            num_samples = max(1, math.floor(target_duration / s_dur))

        gap = (target_duration - (s_dur * num_samples)) / (num_samples + 1)
        valid_points = []
        for i in range(num_samples):
            p = max(0.0, float(math.floor(gap * (i + 1) + s_dur * i)) if s_dur >= 2.0 else (gap * (i + 1) + s_dur * i))
            if p + s_dur > target_duration:
                p = max(0.0, target_duration - s_dur)
            if p not in valid_points:
                valid_points.append(p)

        return s_dur, valid_points or [0.0]

    def get_shared_cache_settings(self, crf_val, seek_offset, target_duration):
        s = self.current_job_settings
        engine = s.get("engine", "FFmpeg")
        return {
            "cache_schema": config.CACHE_SCHEMA_VERSION,
            "tools": tool_fingerprint(config.FFMPEG_EXE, config.FFVSHIP_EXE, config.HANDBRAKE_EXE),
            "crf": f"{crf_val:g}" if isinstance(crf_val, float) else str(crf_val), "engine": engine, "encoder": s.get("encoder"), "preset": s.get("preset"), "tune": s.get("tune"),
            "params": s.get("svt_params") if s.get("encoder") == "SVT-AV1" else s.get("x265_params"), "bit_depth": s.get("bit_depth"),
            "gop_mode": s.get("gop_mode"), "gop_custom_sec": s.get("gop_custom_sec"), "scale": s.get("scale"), "deint": s.get("deint"), "auto_crop": s.get("auto_crop"),
            "sample_dur": s.get("sample_dur"), "sample_int": s.get("sample_int"), "samples_count": s.get("samples_count"), "seek_offset": seek_offset, "target_duration": target_duration,
            # Everything below changes which pixels are encoded/compared, so it must be part of the key.
            # The resolved values are used (not just the UI toggles): the detected crop rectangle, the
            # final keyint and the exact filter chains that both the samples and the references get.
            "custom_vf": (s.get("custom_vf") or "").strip(),
            "custom_hb": (s.get("custom_hb") or "").strip() if engine == "HandBrakeCLI" else "",
            "gop_frames": getattr(self, "current_gop_frames", None),
            "crop": (getattr(self, "current_crop", None) or "") if s.get("auto_crop") else "",
            "sample_vf_chain": self.get_sample_filters(),
            "ref_vf_chain": self.get_exact_ref_filter(),
            # Size-limit calculations use video-stream bytes, not whole-container bytes.
            "size_estimation": "video-stream-v1",
        }

    def extract_and_log_encoder_info(
        self, input_file, file_name, out_fmt_pref, r_mode, seek_start, encode_duration, crf_override=None
    ):
        s = self.current_job_settings
        engine = s.get("engine", "FFmpeg")
        enc_name = s.get("encoder", "SVT-AV1")
        depth_str = s.get("bit_depth", "10-bit")
        active_params = s.get("x265_params", "") if enc_name == "x265 (HEVC)" else s.get("svt_params", "")
        if gop_frames := getattr(self, "current_gop_frames", None):
            active_params = (
                active_params.strip() + f":keyint={gop_frames}"
            ) if active_params.strip() else f"keyint={gop_frames}"
        crf_cmd_val = f"{crf_override:g}" if isinstance(crf_override, (int, float)) else str(s.get("crf", "34.0"))
        crf_log_val = f"{crf_override:g}" if isinstance(crf_override, (int, float)) else (
            str(crf_override) if crf_override is not None else str(s.get("crf", "34.0"))
        )
        encoder_lines, enc_version = [], None

        if engine == "FFmpeg":
            cmd = [config.FFMPEG_EXE, "-y"]
            if seek_start > 0:
                cmd.extend(["-ss", str(seek_start)])
            cmd.extend(["-i", input_file])
            vf_list = []
            if ref_vf := self.get_reference_filters():
                vf_list.append(ref_vf)
            if s.get("deint", False):
                vf_list.append("yadif=1")
            if cust_vf := s.get("custom_vf", "").strip():
                vf_list.append(cust_vf)
            if vf_list:
                cmd.extend(["-vf", ",".join(vf_list)])
            pix_fmt = "yuv420p12le" if "12-bit" in depth_str else ("yuv420p" if "8-bit" in depth_str else "yuv420p10le")

            if enc_name == "x265 (HEVC)":
                cmd.extend(["-c:v", "libx265", "-crf", crf_cmd_val, "-pix_fmt", pix_fmt])
                if preset := s.get("preset", ""):
                    cmd.extend(["-preset", preset])
                if tune_val := s.get("tune", "").strip():
                    cmd.extend(["-tune", tune_val])
                if active_params:
                    cmd.extend(["-x265-params", active_params])
                if gop_frames is not None:
                    cmd.extend(["-g", str(gop_frames)])
            else:
                cmd.extend(
                    ["-c:v", "libsvtav1", "-crf", crf_cmd_val, "-pix_fmt", pix_fmt, "-preset", s.get("preset", "6")]
                )
                params_list = [p for p in active_params.split(":") if p and not p.startswith("tune=")]
                if tune_val := s.get("tune", "").strip():
                    params_list.insert(0, f"tune={tune_val.split(' ')[0].strip() if '(' in tune_val else tune_val}")
                if final_svt_p := ":".join(params_list):
                    cmd.extend(["-svtav1-params", final_svt_p])
                if gop_frames is not None:
                    cmd.extend(["-g", str(gop_frames)])
                active_params = final_svt_p

            cmd.extend(["-vframes", "1", "-f", "null", "-"])
            self.update_status("Status: Fetching encoder specific details (FFmpeg)...")
            proc = self._run_tracked(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                universal_newlines=True
            )
            for line in proc.stderr.splitlines():
                line = line.strip()
                if "Svt[info]:" in line:
                    val = line.split("Svt[info]:", 1)[1].strip()
                    if val.startswith("SVT [version]:"):
                        enc_version = val.replace("SVT [version]:", "").strip()
                    elif val.startswith("SVT [config]:") and (config_val := val.replace("SVT [config]:", "").strip()):
                        encoder_lines.append(config_val)
                elif "x265 [info]:" in line:
                    val = line.split("x265 [info]:", 1)[1].strip()
                    if val.startswith("HEVC encoder version"):
                        enc_version = val
                    else:
                        encoder_lines.append(val)
        else:
            self.update_status("Status: Reading generic encoder details (HandBrakeCLI)...")
            fd, tmp_out = tempfile.mkstemp(suffix=".mkv")
            os.close(fd)
            hb_cmd = self.get_handbrake_cmd(
                input_file,
                tmp_out,
                encode_duration=0,
                seek_start=0.0,
                apply_trim=False,
                with_audio=False,
                crf_override=crf_cmd_val
            )
            hb_cmd.extend(["--start-at", "frames:0", "--stop-at", "frames:1"])
            try:
                for line in self._run_tracked(
                    hb_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
                ).stdout.splitlines():
                    line = line.strip()
                    if "Svt[info]:" in line:
                        val = line.split("Svt[info]:", 1)[1].strip()
                        if val.startswith("SVT [version]:"):
                            enc_version = val.replace("SVT [version]:", "").strip()
                        elif val.startswith("SVT [config]:") and (config_val := val.replace("SVT [config]:", "").strip()):
                            encoder_lines.append(config_val)
                    elif "x265 [info]:" in line:
                        val = line.split("x265 [info]:", 1)[1].strip()
                        if val.startswith("HEVC encoder version"):
                            enc_version = val
                        else:
                            encoder_lines.append(val)
                    elif not enc_version and "HandBrake" in line and "master" in line:
                        enc_version = line.strip()
            except Exception:
                pass
            finally:
                silent_remove(tmp_out)
            if enc_name == "SVT-AV1":
                active_params = ":".join([p for p in active_params.split(":") if p and not p.startswith("tune=")])

        self.log("\n [ Video Settings ]", tag="header")
        self.log(f"   - Engine         : {engine}", tag="svt_cfg")
        self.log(f"   - Container      : {out_fmt_pref.upper()}", tag="svt_cfg")
        self.log(f"   - Encoder        : {enc_name}", tag="svt_cfg")
        if enc_version:
            self.log(f"   - Version        : {enc_version}", tag="svt_cfg")
        self.log(f"   - CRF            : {crf_log_val}", tag="svt_cfg")
        self.log(f"   - Preset         : {s.get('preset', '')}", tag="svt_cfg")
        self.log(f"   - Tune           : {s.get('tune', '')}", tag="svt_cfg")
        self.log(f"   - Bit-Depth      : {s.get('bit_depth', '')}", tag="svt_cfg")
        self.log(f"   - Params         : {active_params}", tag="svt_cfg")
        if gop_frames is not None:
            self.log(f"   - Auto GOP       : ~{gop_frames} frames", tag="svt_cfg")
        for eline in encoder_lines:
            self.log(f"   - {eline}", tag="svt_cfg")
        if r_mode != "Full Video":
            self.log(
                f"   - Trim Active    : Start={format_duration(seek_start)} | Length={format_duration(encode_duration)}",
                tag="warning"
            )

        active_vfs = [
            f for f in [
                s.get("scale", "Original") if s.get("scale", "Original") != "Original" else "",
                "Auto Crop" if s.get("auto_crop", False) and (self.current_crop or engine == "HandBrakeCLI") else "",
                "Yadif (Deinterlace)" if s.get("deint", False) else "",
                f"Auto {s.get('fade_mode', 'None')} ({s.get('fade_dur', '1.5')}s)" if s.get(
                    "fade_mode", "None"
                ) != "None" and engine == "FFmpeg" else "",
                s.get("custom_vf", "").strip() if engine == "FFmpeg" else ""
            ] if f
        ]
        if active_vfs:
            self.log(f"   - V-Filters      : {', '.join(active_vfs)}", tag="svt_cfg")
        if s.get("custom_af", "").strip() and engine == "FFmpeg":
            self.log(f"   - A-Filters      : {s.get('custom_af', '').strip()}", tag="svt_cfg")
        if s.get("custom_hb", "").strip() and engine == "HandBrakeCLI":
            self.log(f"   - HB Params      : {s.get('custom_hb', '').strip()}", tag="svt_cfg")

        a_mix, a_codec = s.get("audio_mixdown", "Auto"), s.get("audio_codec", "OPUS")
        self.log(
            f"   - Audio          : Track={s.get('audio_track', '')} | {a_codec} | {s.get('audio_br', '')} | {a_mix}",
            tag="svt_cfg"
        )

        header_file = (
            f" [ Video Settings ]\n   - Engine         : {engine}\n   - Encoder        : {enc_name}\n"
            f"   - Container      : {out_fmt_pref.upper()}\n"
        )
        if enc_version:
            header_file += f"   - Version        : {enc_version}\n"
        header_file +=(
            f"   - CRF            : {crf_log_val}\n   - Preset         : {s.get('preset', '')}\n"
            f"   - Tune           : {s.get('tune', '')}\n   - Bit-Depth      : {s.get('bit_depth', '')}\n"
            f"   - Params         : {active_params}\n"
        )
        if gop_frames is not None:
            header_file += f"   - Auto GOP       : ~{gop_frames} frames\n"
        for eline in encoder_lines:
            header_file += f"   - {eline}\n"
        if r_mode != "Full Video":
            header_file +=(
                f"   - Target Range   : Mode={r_mode} | Start={format_duration(seek_start)} | "
                f"Length={format_duration(encode_duration)}\n"
            )
        if active_vfs:
            header_file += f"   - V-Filters      : {', '.join(active_vfs)}\n"
        if s.get("custom_af", "").strip() and engine == "FFmpeg":
            header_file += f"   - A-Filters      : {s.get('custom_af', '').strip()}\n"
        if s.get("custom_hb", "").strip() and engine == "HandBrakeCLI":
            header_file += f"   - HB Params      : {s.get('custom_hb', '').strip()}\n"
        header_file +=(
            f"\n [ Audio Settings ]\n   - Track          : {s.get('audio_track', '')}\n"
            f"   - Codec          : {a_codec}\n   - Bitrate        : {s.get('audio_br', '')}\n"
            f"   - Mixdown        : {a_mix}\n\n"
        )
        return header_file

    def run_auto_crf_search(self, input_file, target_duration, file_dir, name_we, seek_offset=0.0):
        self.current_paused_duration = 0.0
        s = self.current_job_settings

        try:
            target_metric = s.get("autocrf_metric", "VMAF").strip()
            target_score = float(str(s.get("autocrf_score", "93.0")).strip())
            min_crf = float(str(s.get("autocrf_min", "20.0")).strip())
            max_crf = float(str(s.get("autocrf_max", "40.0")).strip())
            max_pct = float(str(s.get("autocrf_size", "30")).strip())
        except ValueError:
            self.log("[!] Invalid Auto-CRF parameters. Falling back to default CRF.", tag="error")
            return None, {}, 0.0, 0.0, False

        is_lower_better = (target_metric.upper() == "BUTTERAUGLI")
        cache_settings = self.get_shared_cache_settings(0, seek_offset, target_duration)
        cache_settings.update(
            {"metric": target_metric, "score": target_score, "min_crf": min_crf, "max_crf": max_crf, "max_pct": max_pct}
        )
        sample_duration, sample_points = self.get_sampling_settings(target_duration)

        if s.get("use_cache", True):
            if cached_result := check_cache(input_file, cache_settings, "autocrf"):
                self.log(
                    f"\n[!] Auto-CRF Cache Hit! Restoring previous search history "
                    f"({target_metric} target {target_score:g}, size limit {max_pct:g}%, CRF {min_crf:g}-{max_crf:g}). "
                    "Untick 'Use Cache' to force a fresh search.",
                    tag="q_super"
                )
                self.log(f"\n--- Starting Iterative Auto-CRF Search (CACHED) ---", tag="header")
                self.log(
                    f" -> Goal: Find highest CRF where {target_metric} is {'≤' if is_lower_better else '≥'} "
                    f"{target_score}"
                )
                if max_pct > 0:
                    self.log(f" -> Constraint: Estimated Output Size Must Be \u2264 {max_pct}% Of Original")
                self.log(f" -> Using {len(sample_points)} Sample Segments Per CRF Test")
                for h in cached_result.get("history", []):
                    self.log(
                        f" ├─ CRF {h['crf']:g}\n"
                        f" │    └─ Avg {target_metric}: {h['score']:.2f} (Est. Size: {h['pct']:.1f}%)"
                    )
                    if h.get("log_msg"):
                        self.log(h["log_msg"], tag=h["log_tag"])
                if cached_result.get("success"):
                    self.log(
                        f"\n -> Auto-CRF Search Complete. Selected Optimal CRF: {cached_result['best_crf']:g}",
                        tag="q_super"
                    )
                    return cached_result["best_crf"], cached_result["scores"], cached_result["est_size"], cached_result[
                        "est_time"
                    ], True
                else:
                    self.log(f"\n -> Auto-CRF Search Complete. No valid CRF found within constraints.", tag="error")
                    if cached_result.get("best_crf") is not None:
                        self.log(
                            f" -> Returning last tested CRF for fallback: {cached_result['best_crf']:g}", tag="warning"
                        )
                    return cached_result.get("best_crf"), cached_result.get("scores", {}), cached_result.get(
                        "est_size", 0.0
                    ), cached_result.get("est_time", 0.0), False

        self.log(f"\n--- Starting Iterative Auto-CRF Search ---", tag="header")
        self.log(
            f" -> Goal: Find highest CRF where {target_metric} is {'≤' if is_lower_better else '≥'} {target_score}"
        )
        if max_pct > 0:
            self.log(" -> Size prediction: AB-AV1 compatible (min duration-based, file-percent)")
        if max_pct > 0:
            self.log(f" -> Constraint: Estimated Output Size Must Be \u2264 {max_pct}% Of Original")
        self.log(f" -> Using {len(sample_points)} Sample Segments Per CRF Test")

        work_dir = os.path.join(
            tempfile.gettempdir() if s.get("samples_loc", "System Temp") == "System Temp" else file_dir,
            ".QualiSVT",
            f"{name_we}.autocrf"
        )
        os.makedirs(work_dir, exist_ok=True)
        vid_fps = get_fps(input_file) or 24.0
        depth_str = s.get("bit_depth", "10-bit")
        pix_fmt = "yuv420p12le" if "12-bit" in depth_str else ("yuv420p" if "8-bit" in depth_str else "yuv420p10le")
        ref_pix_fmt = get_video_pix_fmt(input_file) or "yuv420p"
        if ref_pix_fmt not in {
            "yuv420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le", "yuv420p12le", "yuv422p12le",
            "yuv444p12le"
        }:
            ref_pix_fmt = "yuv420p"

        has_ffvship = (target_metric.upper() in ["SSIMULACRA2", "BUTTERAUGLI", "CVVDP"])
        sample_vfs = self.get_sample_filters()
        use_ffv1_ref = bool(sample_vfs and has_ffvship)

        orig_samples = []
        filtered_refs = [None] * len(sample_points)
        encoded_dims = [None] * len(sample_points)
        input_vid_dur = max(1.0, get_duration(input_file))
        input_file_size = os.path.getsize(input_file)
        total_actual_sample_duration = 0
        # AB-AV1-compatible size prediction uses the smaller of:
        #   1) duration-based prediction: encoded sample bytes / sample duration * full duration
        #   2) file-percent prediction: source file bytes * (encoded sample bytes / source sample bytes)
        # The resulting predicted bytes are then divided by the FULL input file size for
        # --max-encoded-percent-style constraint checking.
        source_sample_sizes = []

        try:
            for i, point in enumerate(sample_points):
                if self.cancel_requested:
                    return None, {}, 0.0, 0.0, False
                s_idx, s_tot = i + 1, len(sample_points)
                actual_sample_duration = max(1.0, min(sample_duration, target_duration - point))
                total_actual_sample_duration += actual_sample_duration
                seek_point_in_orig = seek_offset + point
                orig_sample = os.path.join(work_dir, f"sample{i:02d}_orig.mkv")
                filtered_ref = os.path.join(work_dir, f"sample{i:02d}_filtered_ref.mkv")
                source_sample_sizes.append(0)  # populated after the sample is extracted

                self.update_status(
                    f"Status: Auto-CRF - Extracting {'Filtered Ref' if use_ffv1_ref else 'Sample'} {s_idx}/{s_tot}..."
                )
                self.extract_video_chunk(
                    input_file,
                    filtered_ref if use_ffv1_ref else orig_sample,
                    seek_point_in_orig,
                    actual_sample_duration,
                    vid_fps,
                    pix_fmt,
                    vf_string=sample_vfs if use_ffv1_ref else None,
                    use_ffv1=use_ffv1_ref,
                    exact_cfr=use_ffv1_ref
                )
                orig_samples.append(filtered_ref if use_ffv1_ref else orig_sample)
                filtered_refs[i] = filtered_ref if use_ffv1_ref else orig_sample
                try:
                    source_sample_sizes[i] = os.path.getsize(orig_samples[i])
                except OSError:
                    source_sample_sizes[i] = 0

            total_source_sample_size = sum(source_sample_sizes)
            crf_inc = 0.5
            min_q, max_q = int(round(min_crf / crf_inc)), int(round(max_crf / crf_inc))
            q = (min_q + max_q) // 2
            best_crf, best_res, last_tested_crf, last_tested_res, history, run_count = None, None, None, None, [], 1
            cut_on_iter2 = (max_crf - min_crf) > 23.0

            def vmaf_lerp_q(target, w_samp, b_samp):
                diff = b_samp["score"] - w_samp["score"]
                if diff == 0:
                    return (w_samp["q"] + b_samp["q"]) // 2
                return max(
                    b_samp["q"] + 1,
                    min(
                        w_samp["q"] - 1,
                        int(round(w_samp["q"] - (w_samp["q"] - b_samp["q"]) * ((target - w_samp["score"]) / diff)))
                    )
                )

            while True:
                if self.cancel_requested:
                    break
                q = max(min_q, min(max_q, q))
                next_crf = q * crf_inc
                self.update_status(f"Status: Auto-CRF - Testing CRF {next_crf:g}...")
                self.log(f" ├─ CRF {next_crf:g}")

                shared_settings = self.get_shared_cache_settings(next_crf, seek_offset, target_duration)
                shared_cached = check_cache(input_file, shared_settings, "crf_eval") if s.get(
                    "use_cache", True
                ) else None

                if shared_cached and target_metric in shared_cached.get("scores", {}):
                    cached_est_size = float(shared_cached.get("predicted_size", shared_cached.get("est_size", 0)) or 0)
                    cached_est_time = float(shared_cached.get("est_time", 0) or 0)
                    res = (
                        shared_cached["scores"][target_metric],
                        cached_est_size,
                        (
                            cached_est_time / target_duration
                        ) * total_actual_sample_duration if target_duration > 0 else cached_est_time,
                        (cached_est_size / input_file_size) * 100 if input_file_size > 0 else 0
                    )
                    self.log(f" │    └─ Avg {target_metric}: {res[0]:.2f} (Est. Size: {res[3]:.1f}%) [Cache Hit]")
                else:
                    total_enc_size, total_enc_time, all_scores, filter_error = 0, 0, [], False
                    for i, point in enumerate(sample_points):
                        if self.cancel_requested:
                            break
                        s_idx, s_tot = i + 1, len(sample_points)
                        enc_sample = os.path.join(work_dir, f"sample{i:02d}_enc.mkv")
                        actual_sample_duration = max(1.0, min(sample_duration, target_duration - point))

                        enc_cmd = (
                            self.get_handbrake_cmd(
                                orig_samples[i], enc_sample, apply_trim=False, with_audio=False, crf_override=next_crf
                            )
                            if s.get("engine", "FFmpeg") == "HandBrakeCLI"
                            else self.get_ffmpeg_cmd(
                                orig_samples[i],
                                enc_sample,
                                actual_sample_duration,
                                is_sample=True,
                                with_audio=False,
                                skip_filters=use_ffv1_ref,
                                crf_override=next_crf
                            )
                        )
                        start_enc_time = time.time()

                        filter_ok = self.run_command_with_progress(
                            enc_cmd,
                            actual_sample_duration,
                            work_dir,
                            f"Auto-CRF Encoding Chunk {s_idx}/{s_tot} (CRF {next_crf:g})",
                            engine=s.get("engine", "FFmpeg")
                        )
                        total_enc_time += self.get_adjusted_elapsed_time(start_enc_time)
                        if self.cancel_requested:
                            break

                        # Measure only the encoded video stream, matching the size
                        # basis used by AB-AV1 for --max-encoded-percent.
                        enc_size = get_video_stream_size(enc_sample) if os.path.exists(enc_sample) else 0
                        if enc_size <= 0 and os.path.exists(enc_sample):
                            enc_size = os.path.getsize(enc_sample)
                        total_enc_size += enc_size

                        if encoded_dims[i] is None:
                            encoded_dims[i] = get_video_dimensions(enc_sample)
                        enc_w, enc_h = encoded_dims[i]

                        if filter_ok:
                            qual_score, _, f_err = self.get_metric_score(
                                target_metric,
                                enc_sample,
                                filtered_refs[i] if has_ffvship else orig_samples[i],
                                actual_sample_duration,
                                vid_fps,
                                ref_pix_fmt,
                                "" if has_ffvship else self.get_exact_ref_filter(),
                                work_dir,
                                s_idx,
                                s_tot,
                                enc_w,
                                enc_h,
                                progress_label=f"Auto-CRF Eval Chunk {s_idx}/{s_tot}"
                            )
                            if f_err:
                                filter_error = True
                        else:
                            qual_score = None

                        if qual_score is not None:
                            all_scores.append(qual_score)
                            sample_file_size = source_sample_sizes[i] if i < len(source_sample_sizes) else 0
                            sample_pct = (enc_size / sample_file_size * 100) if sample_file_size > 0 else 0
                            self.log(
                                f" │    {'└─' if s_idx == s_tot else '├─'} Sample {s_idx}/{s_tot}: {target_metric} "
                                f"{qual_score:.2f}, Size {sample_pct:.1f}%"
                            )
                            tune = s.get("tune", "")
                            target_path = os.path.join(
                                work_dir,
                                f"sample{i:02d}+{target_metric.lower()}.{qual_score:.2f}.{'hevc' if 'x265' in s.get('encoder', 'SVT-AV1') else 'av1'}.crf{next_crf}_{s.get('preset', '')}_{tune.split('(')[1].replace(')','').strip() if '(' in tune else tune}.mkv"
                            )
                            if os.path.exists(enc_sample):
                                try:
                                    os.replace(enc_sample, target_path)
                                except Exception:
                                    pass
                        else:
                            self.log(f" │    {'└─' if s_idx == s_tot else '├─'} Sample {s_idx}/{s_tot}: Failed")

                    if self.cancel_requested:
                        break
                    if not all_scores or filter_error:
                        res = (None, 0, 0, 0)
                    else:
                        duration_est = (
                            total_enc_size / total_actual_sample_duration
                        ) * target_duration if total_actual_sample_duration > 0 else 0
                        file_percent_est = (
                            input_file_size * total_enc_size / total_source_sample_size
                        ) if total_source_sample_size > 0 else duration_est
                        predicted_size = min(duration_est, file_percent_est)
                        res = (
                            sum(all_scores) / len(all_scores),
                            predicted_size,
                            total_enc_time,
                            (predicted_size / input_file_size) * 100 if input_file_size > 0 else 0
                        )
                        try:
                            save_cache(
                                input_file,
                                shared_settings,
                                {
                                    "scores": {target_metric: res[0]},
                                    "predicted_size": res[1],
                                    "est_size": res[1],
                                    "est_time": (
                                        total_enc_time / total_actual_sample_duration
                                    ) * target_duration if total_actual_sample_duration > 0 else 0
                                },
                                "crf_eval"
                            )
                        except Exception:
                            pass

                    if res[0] is None:
                        self.log(f" │    └─ CRF {next_crf:g} Failed: Metric calculation error.", tag="error")
                        break
                    self.log(f" │    └─ Avg {target_metric}: {res[0]:.2f} (Est. Size: {res[3]:.1f}%)")

                size_ok = max_pct <= 0 or res[3] <= max_pct
                qual_ok = (res[0] <= target_score) if is_lower_better else (res[0] >= target_score)
                current_sample = {"crf": next_crf, "q": q, "score": res[0], "pct": res[3], "res": res}
                history.append(current_sample)
                last_tested_crf, last_tested_res = next_crf, res

                status_text, log_col = (
                    ("Target Met!", "q_vlossless")
                    if size_ok
                    and qual_ok
                    else (f"Rejected: Size ({res[3]:.1f}%) & Quality ({res[0]:.2f})", "error")
                    if not size_ok
                    and not qual_ok
                    else (f"Rejected: Size ({res[3]:.1f}%)", "warning")
                    if not size_ok
                    else (f"Rejected: {target_metric} ({res[0]:.2f})", "warning")
                )
                higher_tolerance = max(0.1, crf_inc * (2 ** (run_count - 1)) * 0.1)
                action_text, is_done = "", False

                if qual_ok:
                    if size_ok and abs(target_score - res[0]) < higher_tolerance:
                        action_text, best_crf, best_res, is_done = "Optimal CRF Accepted.", next_crf, res, True
                    else:
                        upper_candidates = [x for x in history if x["q"] > q]
                        if upper_candidates:
                            upper = min(upper_candidates, key=lambda x: x["q"])
                            if upper["q"] == q + 1:
                                action_text, log_col, best_crf, best_res, is_done = (
                                    (
                                        "Best achievable within constraints. Accepted.",
                                        "q_vlossless",
                                        next_crf,
                                        res,
                                        True
                                    )
                                    if size_ok
                                    else (
                                        "Failed constraints. Unable to increase CRF.", "error", best_crf, best_res, True
                                    )
                                )
                            else:
                                q = vmaf_lerp_q(target_score, upper, current_sample)
                                action_text = (
                                    "Interpolating next CRF: "
                                    f"{vmaf_lerp_q(target_score, upper, current_sample) * crf_inc:g}"
                                )
                                log_col = "q_high"
                        else:
                            if q == max_q:
                                action_text, log_col, best_crf, best_res, is_done = (
                                    ("Max CRF reached. Accepted.", "q_vlossless", next_crf, res, True)
                                    if size_ok
                                    else ("Max CRF reached but size is too large.", "error", best_crf, best_res, True)
                                )
                            elif cut_on_iter2 and run_count == 1 and q + 1 < max_q:
                                q, action_text, log_col = int(
                                    round(q * 0.4 + max_q * 0.6)
                                ), f"Jumping to {int(round(q * 0.4 + max_q * 0.6)) * crf_inc:g}", "q_high"
                            else:
                                q, action_text, log_col = max_q, f"Testing Max CRF: {max_q * crf_inc:g}", "q_high"
                else:
                    if (not size_ok) or (q == min_q):
                        action_text, log_col, is_done = "Impossible constraints or Min CRF reached.", "error", True
                    else:
                        lower_candidates = [x for x in history if x["q"] < q]
                        if lower_candidates:
                            lower = max(lower_candidates, key=lambda x: x["q"])
                            if lower["q"] + 1 == q:
                                action_text, log_col, best_crf, best_res, is_done = (
                                    (
                                        f"Returning to last good CRF {lower['crf']:g}.",
                                        "q_super",
                                        lower["crf"],
                                        lower["res"],
                                        True
                                    )
                                    if (max_pct <= 0 or lower["pct"] <= max_pct)
                                    else ("No valid CRF left.", "error", best_crf, best_res, True)
                                )
                            else:
                                q = vmaf_lerp_q(target_score, current_sample, lower)
                                action_text = (
                                    "Interpolating next CRF: "
                                    f"{vmaf_lerp_q(target_score, current_sample, lower) * crf_inc:g}"
                                )
                        else:
                            q, action_text = (
                                (
                                    int(round(q * 0.4 + min_q * 0.6)),
                                    f"Jumping to {int(round(q * 0.4 + min_q * 0.6)) * crf_inc:g}"
                                )
                                if cut_on_iter2
                                and run_count == 1
                                and q > min_q + 1
                                else (min_q, f"Testing Min CRF: {min_q * crf_inc:g}")
                            )

                current_sample["log_msg"], current_sample[
                    "log_tag"
                ] = f" │         └─ CRF {next_crf:g} [{status_text}] -> {action_text}", log_col
                self.log(current_sample["log_msg"], tag=current_sample["log_tag"])
                if is_done:
                    break
                run_count += 1

        except Exception as e:
            if not self.cancel_requested:
                self.log(f" -> Auto-CRF Error: {e}", tag="error")
        finally:
            if not s.get("keep_samples", False):
                shutil.rmtree(work_dir, ignore_errors=True)

        if self.cancel_requested:
            return None, {}, 0.0, 0.0, False
        if best_crf is None:
            self.log(f"\n -> Auto-CRF Search Complete. No valid CRF found within constraints.", tag="error")
            if last_tested_crf is not None and last_tested_res:
                self.log(f" -> Returning last tested CRF for fallback: {last_tested_crf:g}", tag="warning")
                est_total_size = last_tested_res[1]
                est_total_time = (
                    last_tested_res[2] / total_actual_sample_duration
                ) * target_duration if total_actual_sample_duration > 0 else 0
                try:
                    save_cache(
                        input_file,
                        cache_settings,
                        {
                            "success": False,
                            "best_crf": last_tested_crf,
                            "scores": {target_metric: last_tested_res[0]},
                            "est_size": est_total_size,
                            "est_time": est_total_time,
                            "history": history
                        },
                        "autocrf"
                    )
                except Exception:
                    pass
                return last_tested_crf, {
                    target_metric: last_tested_res[0] if last_tested_res[0] is not None else 0.0
                }, est_total_size, est_total_time, False
            try:
                save_cache(
                    input_file, cache_settings, {"success": False, "best_crf": None, "history": history}, "autocrf"
                )
            except Exception:
                pass
            return None, {}, 0.0, 0.0, False

        self.log(f"\n -> Auto-CRF Search Complete. Selected Optimal CRF: {best_crf:g}", tag="q_super")
        est_total_size = best_res[1]
        est_total_time = (
            best_res[2] / total_actual_sample_duration
        ) * target_duration if total_actual_sample_duration > 0 else 0
        try:
            save_cache(
                input_file,
                cache_settings,
                {
                    "success": True,
                    "best_crf": best_crf,
                    "scores": {target_metric: best_res[0]},
                    "est_size": est_total_size,
                    "est_time": est_total_time,
                    "history": history
                },
                "autocrf"
            )
        except Exception:
            pass
        return best_crf, {target_metric: best_res[0]}, est_total_size, est_total_time, True

    def run_pre_quality_test(self, input_file, target_duration, file_dir, name_we, seek_offset=0.0, crf_override=None):
        self.current_paused_duration, test_start_time = 0.0, time.time()
        s = self.current_job_settings
        selected_metrics = [m for m, v in self.current_job_settings.get("metrics", {}).items() if v]
        if not selected_metrics:
            self.log(" -> No metrics selected. Skipping Pre-Encode test.", tag="warning")
            return {}, 0.0, 0.0

        crf_to_use = crf_override if crf_override is not None else float(str(s.get("crf", "34.0")))
        shared_settings = self.get_shared_cache_settings(crf_to_use, seek_offset, target_duration)
        missing_metrics, cached_scores, est_total_size, est_total_time = selected_metrics.copy(), {}, 0, 0

        if s.get("use_cache", True):
            if shared_cached := check_cache(input_file, shared_settings, "crf_eval"):
                est_total_size, est_total_time = shared_cached.get("est_size", 0), shared_cached.get("est_time", 0)
                for m in selected_metrics:
                    if m in shared_cached.get("scores", {}):
                        cached_scores[m] = shared_cached["scores"][m]
                        if m in missing_metrics:
                            missing_metrics.remove(m)
                if not missing_metrics:
                    self.log(f"\n[!] Estimation Cache Hit! Restoring results...", tag="q_super")
                    self.log(f"\n--- Pre-Encode Estimation (CACHED) ---", tag="header")
                    for m in selected_metrics:
                        self.log(
                            f" ├─ Predicted {m} Score: {cached_scores[m]:.2f} "
                            f"[{get_quality_label(m, cached_scores[m])}]",
                            tag=get_quality_color_tag(get_quality_label(m, cached_scores[m]))
                        )
                    self.log(
                        f" ├─ Predicted Output Size   : {est_total_size / (1024*1024):.2f} MB "
                        f"({((est_total_size / os.path.getsize(input_file)) * 100) if os.path.exists(input_file) and os.path.getsize(input_file) > 0 else 0:.0f}%)"
                    )
                    self.log(f" └─ Predicted Encoding Time : {format_duration(est_total_time)}")
                    return cached_scores, est_total_size, est_total_time
                elif cached_scores:
                    self.log(
                        "\n"
                        f"[!] Partial Cache Hit. Cached: {', '.join(cached_scores.keys())}. Need to calculate: "
                        f"{', '.join(missing_metrics)}",
                        tag="info"
                    )

        sample_duration, sample_points = self.get_sampling_settings(target_duration)
        self.log(f"\n--- Starting Pre-Encode Estimation & Quality Test ---", tag="header")
        self.log(
            f" -> Testing Metrics: {', '.join(missing_metrics)}\n"
            f" -> Will test {len(sample_points)} chunk(s) across the video."
        )

        depth_str = s.get("bit_depth", "10-bit")
        pix_fmt = "yuv420p12le" if "12-bit" in depth_str else ("yuv420p" if "8-bit" in depth_str else "yuv420p10le")
        ref_pix_fmt = get_video_pix_fmt(input_file) or "yuv420p"
        if ref_pix_fmt not in {
            "yuv420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le", "yuv420p12le", "yuv422p12le",
            "yuv444p12le"
        }:
            ref_pix_fmt = "yuv420p"

        scores = {m: [] for m in missing_metrics}
        total_sample_size = 0
        total_sample_encode_time = 0
        total_actual_sample_duration = 0
        work_dir = os.path.join(
            tempfile.gettempdir() if s.get("samples_loc", "System Temp") == "System Temp" else file_dir,
            ".QualiSVT",
            f"{name_we}.pre.{'multi' if len(missing_metrics) > 1 else missing_metrics[0].lower()}"
        )
        os.makedirs(work_dir, exist_ok=True)

        tune = s.get("tune", "")
        vid_fps = get_fps(input_file) or 24.0
        has_ffvship = any(m in missing_metrics for m in ["SSIMULACRA2", "Butteraugli", "CVVDP"])
        sample_vfs = self.get_sample_filters()
        use_ffv1_ref = bool(sample_vfs and has_ffvship)
        orig_samples = []

        try:
            for i, point in enumerate(sample_points):
                if self.cancel_requested:
                    return {}, 0.0, 0.0
                actual_sample_duration = max(1.0, min(sample_duration, target_duration - point))
                seek_point_in_orig = seek_offset + point
                target_ref = os.path.join(work_dir, f"sample{i:02d}_{'filtered_ref' if use_ffv1_ref else 'orig'}.mkv")
                self.update_status(
                    f"Status: Pre-Test - Extracting {'Filtered Ref' if use_ffv1_ref else 'Sample'} "
                    f"{i+1}/{len(sample_points)}"
                )
                self.extract_video_chunk(
                    input_file,
                    target_ref,
                    seek_point_in_orig,
                    actual_sample_duration,
                    vid_fps,
                    pix_fmt,
                    vf_string=sample_vfs if use_ffv1_ref else None,
                    use_ffv1=use_ffv1_ref,
                    exact_cfr=use_ffv1_ref
                )
                orig_samples.append(target_ref)

            for i, point in enumerate(sample_points):
                if self.cancel_requested:
                    return {}, 0.0, 0.0
                s_idx = i + 1
                s_tot = len(sample_points)
                actual_sample_duration = max(1.0, min(sample_duration, target_duration - point))
                enc_sample = os.path.join(work_dir, f"sample{i:02d}_enc.mkv")
                orig_size = (
                    (os.path.getsize(input_file) / max(1.0, get_duration(input_file))) * actual_sample_duration
                ) if use_ffv1_ref else (os.path.getsize(orig_samples[i]) if os.path.exists(orig_samples[i]) else 1)

                self.update_status(f"Status: Pre-Test - Encoding Sample {s_idx}/{s_tot}")
                enc_cmd = (
                    self.get_handbrake_cmd(
                        orig_samples[i],
                        enc_sample,
                        encode_duration=0,
                        seek_start=0.0,
                        apply_trim=False,
                        with_audio=False,
                        crf_override=crf_override
                    )
                    if s.get("engine", "FFmpeg") == "HandBrakeCLI"
                    else self.get_ffmpeg_cmd(
                        orig_samples[i],
                        enc_sample,
                        actual_sample_duration,
                        is_sample=True,
                        with_audio=False,
                        skip_filters=use_ffv1_ref,
                        crf_override=crf_override
                    )
                )
                start_enc_time = time.time()

                filter_ok = self.run_command_with_progress(
                    enc_cmd,
                    actual_sample_duration,
                    work_dir,
                    f"Encoding Sample {s_idx}",
                    engine=s.get("engine", "FFmpeg")
                )
                total_sample_encode_time += self.get_adjusted_elapsed_time(start_enc_time)
                total_actual_sample_duration += actual_sample_duration

                enc_size = os.path.getsize(enc_sample) if os.path.exists(enc_sample) else 0
                total_sample_size += enc_size
                size_ratio = (enc_size / orig_size) * 100 if orig_size > 0 else 0

                enc_w, enc_h = get_video_dimensions(enc_sample)
                qual_scores = {}

                for m_name in missing_metrics:
                    if self.cancel_requested:
                        raise Exception("Cancelled")
                    self.update_status(f"Status: Pre-Test - Calc {m_name} {s_idx}/{s_tot}")

                    if filter_ok:
                        qual_score, _, f_err = self.get_metric_score(
                            m_name,
                            enc_sample,
                            orig_samples[i],
                            actual_sample_duration,
                            vid_fps,
                            ref_pix_fmt,
                            "" if has_ffvship else self.get_exact_ref_filter(),
                            work_dir,
                            s_idx,
                            s_tot,
                            enc_w,
                            enc_h,
                            progress_label=f"Calculating {m_name} {s_idx}"
                        )
                    else:
                        qual_score, f_err = None, True

                    if qual_score is not None:
                        qual_scores[m_name] = qual_score
                        scores[m_name].append(qual_score)
                        lbl = get_quality_label(m_name, qual_score)
                        self.log(
                            f" ├─ Sample {s_idx}/{s_tot}: {m_name} = {qual_score:.2f} [{lbl}]",
                            tag=get_quality_color_tag(lbl)
                        )
                    else:
                        self.log(
                            f" ├─ Sample {s_idx}/{s_tot}: {m_name} Failed{' (Dependencies)' if f_err else ''}",
                            tag="error"
                        )

                self.log(f" │    └─ Sample Output Size Ratio: {size_ratio:.0f}%")
                if qual_scores:
                    target_path = os.path.join(
                        work_dir,
                        f"sample{i:02d}+{'+'.join([f'{m.lower()}.{s:.2f}' for m, s in qual_scores.items()])}.{'hevc' if 'x265' in s.get('encoder', 'SVT-AV1') else 'av1'}.crf{f'{crf_override:g}' if isinstance(crf_override, (float, int)) else str(s.get('crf', '34.0'))}_{s.get('preset', '')}_{tune.split('(')[1].replace(')','').strip() if '(' in tune else tune}.mkv"
                    )
                    if os.path.exists(enc_sample):
                        try:
                            os.replace(enc_sample, target_path)
                        except Exception:
                            pass
        except Exception as e:
            if not self.cancel_requested:
                self.log(f" ├─ Error occurred -> {e}", tag="error")
        finally:
            if not s.get("keep_samples", False):
                shutil.rmtree(work_dir, ignore_errors=True)
            else:
                for f in os.listdir(work_dir):
                    if f.endswith(".ffindex"):
                        silent_remove(os.path.join(work_dir, f))

        if not self.cancel_requested:
            final_avg = cached_scores.copy()
            for m in missing_metrics:
                if scores[m]:
                    final_avg[m] = sum(scores[m]) / len(scores[m])

            if total_actual_sample_duration > 0:
                est_total_size = (total_sample_size / total_actual_sample_duration) * target_duration
                est_total_time = (total_sample_encode_time / total_actual_sample_duration) * target_duration
            elif est_total_size == 0 and est_total_time == 0:
                est_total_size, est_total_time = 0.0, 0.0

            for m, avg_qual in final_avg.items():
                self.log(
                    f" ├─ Predicted {m} Score: {avg_qual:.2f} [{get_quality_label(m, avg_qual)}]",
                    tag=get_quality_color_tag(get_quality_label(m, avg_qual))
                )

            input_sz = os.path.getsize(input_file) if os.path.exists(input_file) else 0
            self.log(
                f" ├─ Predicted Output Size   : {est_total_size / (1024*1024):.2f} MB "
                f"({(est_total_size / input_sz) * 100 if input_sz > 0 else 0:.0f}%)"
            )
            self.log(
                f" ├─ Predicted Encoding Time : {format_duration(est_total_time)}\n"
                f" └─ Pre-Test Duration       : {format_duration(self.get_adjusted_elapsed_time(test_start_time))}"
            )
            try:
                save_cache(
                    input_file,
                    shared_settings,
                    {"scores": final_avg, "est_size": est_total_size, "est_time": est_total_time},
                    "crf_eval"
                )
            except Exception:
                pass
            return final_avg, est_total_size, est_total_time
        return {}, 0.0, 0.0

    def run_post_quality_test(self, input_file, encoded_file, target_duration, file_dir, name_we, seek_offset=0.0):
        self.current_paused_duration, test_start_time = 0.0, time.time()
        s = self.current_job_settings
        selected_metrics = [m for m, v in self.current_job_settings.get("metrics", {}).items() if v]
        if not selected_metrics:
            self.log(" -> No metrics selected. Skipping Post-Encode verification.", tag="warning")
            return {}

        sample_duration, sample_points = self.get_sampling_settings(target_duration)
        self.log(f"\n--- Starting Post-Encode Evaluation (Worst/Mid/Best Frames) ---", tag="header")

        depth_str = s.get("bit_depth", "10-bit")
        pix_fmt = "yuv420p12le" if "12-bit" in depth_str else ("yuv420p" if "8-bit" in depth_str else "yuv420p10le")
        scores = {m: [] for m in selected_metrics}
        work_dir = os.path.join(
            tempfile.gettempdir() if s.get("samples_loc", "System Temp") == "System Temp" else file_dir,
            ".QualiSVT",
            f"{name_we}.post.{'multi' if len(selected_metrics) > 1 else selected_metrics[0].lower()}"
        )
        os.makedirs(work_dir, exist_ok=True)
        vid_fps = get_fps(input_file) or 24.0

        try:
            for i, point in enumerate(sample_points):
                if self.cancel_requested:
                    return {}
                s_idx = i + 1
                s_tot = len(sample_points)
                actual_sample_duration = max(1.0, min(sample_duration, target_duration - point))
                chunk_orig = os.path.join(work_dir, f"sample{i:02d}_orig_post.mkv")
                chunk_enc = os.path.join(work_dir, f"sample{i:02d}_enc_post.mkv")

                self.update_status(f"Status: Post-Test - Extracting Original Chunk {s_idx}/{s_tot}")
                self.extract_video_chunk(
                    input_file,
                    chunk_orig,
                    seek_offset + point,
                    actual_sample_duration,
                    vid_fps,
                    pix_fmt,
                    vf_string=self.get_sample_filters(),
                    use_ffv1=True,
                    exact_cfr=True
                )

                self.update_status(f"Status: Post-Test - Extracting Encoded Chunk {s_idx}/{s_tot}")
                self.extract_video_chunk(
                    encoded_file,
                    chunk_enc,
                    point,
                    actual_sample_duration,
                    vid_fps,
                    pix_fmt,
                    vf_string=None,
                    use_ffv1=True,
                    exact_cfr=True
                )

                enc_w, enc_h = get_video_dimensions(chunk_enc)
                for m_name in selected_metrics:
                    if self.cancel_requested:
                        raise Exception("Cancelled")
                    qual_score, frame_scores, f_err = self.get_metric_score(
                        m_name,
                        chunk_enc,
                        chunk_orig,
                        actual_sample_duration,
                        vid_fps,
                        pix_fmt,
                        "",
                        work_dir,
                        s_idx,
                        s_tot,
                        enc_w,
                        enc_h,
                        progress_label=f"Analyzing {m_name} {s_idx}"
                    )

                    if frame_scores:
                        if m_name != "VMAF" or qual_score is None:
                            qual_score = sum(fs[1] for fs in frame_scores) / len(frame_scores)
                        scores[m_name].append(qual_score)

                        sorted_scores = sorted(frame_scores, key=lambda x: x[1])
                        search_pool = sorted_scores[
                            max(1, int(len(sorted_scores) * 0.05)):-max(1, int(len(sorted_scores) * 0.05))
                        ] if len(sorted_scores) >= 20 else sorted_scores
                        worst_f, best_f = (search_pool[-1], search_pool[0]) if m_name.upper() == "BUTTERAUGLI" else (
                            search_pool[0], search_pool[-1]
                        )
                        mid_f = search_pool[len(search_pool) // 2]

                        lbl = get_quality_label(m_name, qual_score)
                        self.log(
                            f" ├─ Sample {s_idx} ({format_duration(point)}): {m_name} = {qual_score:.2f} [{lbl}]\n"
                            f" │    └─ Worst: {worst_f[1]:.2f} | Mid: {mid_f[1]:.2f} | Best: {best_f[1]:.2f}",
                            tag=get_quality_color_tag(lbl)
                        )

                        if s.get("keep_samples", False):
                            for label, f_idx, f_score in [
                                ("Worst", worst_f[0], worst_f[1]),
                                ("Mid", mid_f[0], mid_f[1]),
                                ("Best", best_f[0], best_f[1])
                            ]:
                                self.update_status(f"Status: Extracting {label} Frame ({m_name}) - Sample {s_idx}")
                                if s.get("merge_samples", False):
                                    self.extract_merged_frame_as_webp(
                                        chunk_orig,
                                        chunk_enc,
                                        os.path.join(
                                            work_dir,
                                            f"sample{i:02d}_{label}_{m_name.lower()}.{f_score:.2f}_Comparison.webp"
                                        ),
                                        f_idx
                                    )
                                else:
                                    self.extract_frame_as_webp(
                                        chunk_orig,
                                        os.path.join(
                                            work_dir, f"sample{i:02d}_{label}_{m_name.lower()}.{f_score:.2f}_Orig.webp"
                                        ),
                                        f_idx
                                    )
                                    self.extract_frame_as_webp(
                                        chunk_enc,
                                        os.path.join(
                                            work_dir, f"sample{i:02d}_{label}_{m_name.lower()}.{f_score:.2f}_Enc.webp"
                                        ),
                                        f_idx
                                    )
                    elif qual_score is not None:
                        scores[m_name].append(qual_score)
                        self.log(
                            f" ├─ Sample {s_idx}/{s_tot}: {m_name} = {qual_score:.2f} "
                            f"[{get_quality_label(m_name, qual_score)}]",
                            tag=get_quality_color_tag(get_quality_label(m_name, qual_score))
                        )
                    else:
                        self.log(f" ├─ Sample {s_idx}/{s_tot}: {m_name} = Failed (Log Parsing Issue)", tag="error")

                silent_remove(chunk_orig)
                silent_remove(chunk_enc)

        except Exception as e:
            if not self.cancel_requested:
                self.log(f" ├─ Error occurred -> {e}", tag="error")
        finally:
            if not s.get("keep_samples", False):
                shutil.rmtree(work_dir, ignore_errors=True)
            else:
                for f in os.listdir(work_dir):
                    if f.endswith((".log", ".json", ".mkv", ".ffindex")):
                        silent_remove(os.path.join(work_dir, f))

        if not self.cancel_requested:
            final_avg = {m: sum(scores[m]) / len(scores[m]) for m in selected_metrics if scores[m]}
            for m, avg_qual in final_avg.items():
                self.log(
                    f" ├─ Final Actual {m} Average: {avg_qual:.2f} [{get_quality_label(m, avg_qual)}]",
                    tag=get_quality_color_tag(get_quality_label(m, avg_qual))
                )
            self.log(
                f" └─ Post-Test Duration           : {format_duration(self.get_adjusted_elapsed_time(test_start_time))}"
            )
            return final_avg
        return {}
