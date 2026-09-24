"""Builds the ffmpeg / HandBrakeCLI command lines (codec flags come from encoder plugins)."""
import subprocess

import core.config as config
from core.video_info import has_audio_stream
from plugins.base import manager as plugin_manager
from plugins.encoder_ffmpeg import backend_for_encoder_setting


class CommandBuilderMixin:
    def get_handbrake_cmd(
        self,
        input_file,
        output_file,
        encode_duration=0,
        seek_start=0.0,
        apply_trim=False,
        with_audio=True,
        crf_override=None
    ):
        # PLUGIN HOOK: whole command now built by the HandBrakeCLI backend plugin
        # (it owns its entire command line, unlike the ffmpeg codecs below).
        s = self.current_job_settings
        gop_frames = getattr(self, "current_gop_frames", None)
        backend = plugin_manager.get_encoder("HandBrakeCLI")()
        return backend.build_command(input_file, output_file, s, gop_frames=gop_frames,
                                      encode_duration=encode_duration, seek_start=seek_start,
                                      apply_trim=apply_trim, with_audio=with_audio, crf_override=crf_override)

    def get_ffmpeg_cmd(
        self,
        input_file,
        output_file,
        target_duration=0,
        is_sample=False,
        with_audio=True,
        seek_start=0.0,
        apply_trim=False,
        skip_filters=False,
        crf_override=None
    ):
        s = self.current_job_settings
        cmd = [config.FFMPEG_EXE, "-y"]
        if is_sample:
            cmd.extend(["-hide_banner", "-nostdin"])
        if not is_sample and seek_start > 0:
            cmd.extend(["-ss", str(seek_start)])
        cmd.extend(["-i", input_file])

        # [FIX] Short-circuit so we don't spawn an ffprobe process to check for
        # audio on every sample encode (Auto-CRF/Pre-Test call this per chunk,
        # per CRF step) when with_audio is already False and the result would
        # be discarded anyway.
        actual_with_audio = with_audio and has_audio_stream(input_file)

        vf_list, af_list = [], []
        if not is_sample:
            if ref_vf := self.get_reference_filters():
                vf_list.append(ref_vf)
            if s.get("deint", False):
                vf_list.append("yadif=1")
            fade_mode = s.get("fade_mode", "None")
            if fade_mode != "None" and target_duration > 0:
                try:
                    fdur = float(s.get("fade_dur", "1.5"))
                except Exception:
                    fdur = 1.5
                if fade_mode in ["Fade In", "Both"]:
                    vf_list.append(f"fade=t=in:st=0:d={fdur}")
                    if actual_with_audio:
                        af_list.append(f"afade=t=in:st=0:d={fdur}")
                if fade_mode in ["Fade Out", "Both"]:
                    vf_list.append(f"fade=t=out:st={max(0, target_duration - fdur):.3f}:d={fdur}")
                    if actual_with_audio:
                        af_list.append(f"afade=t=out:st={max(0, target_duration - fdur):.3f}:d={fdur}")
            if cust_vf := s.get("custom_vf", "").strip():
                vf_list.append(cust_vf)
            if cust_af := s.get("custom_af", "").strip():
                if actual_with_audio:
                    af_list.append(cust_af)
        elif not skip_filters:
            if sample_vfs := self.get_sample_filters():
                vf_list.append(sample_vfs)

        # [FIX] Video stream mapping is now separated from audio so subtitles can
        # be preserved even when the source has no audio (previously "-sn" in the
        # no-audio branch silently dropped all subtitles on the main encode).
        if actual_with_audio:
            cmd.extend(
                ["-map", "0:v:0", "-map", "0:a:0?"]
                if s.get("audio_track", "") == "Track 1 (Default)"
                else ["-map", "0:v:0", "-map", "0:a?"]
            )
        else:
            cmd.extend(["-map", "0:v:0", "-an"])

        # Subtitle mapping (independent of audio presence).
        if (output_file.lower().endswith(".mp4") or (not is_sample and s.get("out_format") == "MP4")):
            try:
                res = subprocess.run(
                    [
                        config.FFPROBE_EXE,
                        "-v",
                        "error",
                        "-select_streams",
                        "s",
                        "-show_entries",
                        "stream=codec_name",
                        "-of",
                        "csv=p=0",
                        input_file
                    ],
                    stdout=subprocess.PIPE,
                    text=True,
                    errors="replace",
                    creationflags=config.C_FLAGS
                )
                codecs = [c.strip().lower() for c in res.stdout.splitlines() if c.strip()]
                text_codecs = {"subrip", "ass", "ssa", "mov_text", "srt", "webvtt", "text"}
                mapped_any = False
                for idx, c in enumerate(codecs):
                    if c in text_codecs:
                        cmd.extend(["-map", f"0:s:{idx}"])
                        mapped_any = True
                if mapped_any:
                    cmd.extend(["-c:s", "mov_text"])
            except Exception:
                pass
        else:
            cmd.extend(["-map", "0:s?", "-c:s", "copy"])

        # Audio encoding (only if we have audio).
        if actual_with_audio:
            a_codec, audio_br = s.get("audio_codec", "OPUS"), s.get("audio_br", "128K")
            if a_codec == "OPUS":
                cmd.extend(["-c:a", "libopus", "-b:a", audio_br, "-vbr", "on", "-compression_level", "10"])
            elif a_codec == "AAC":
                cmd.extend(["-c:a", "aac", "-b:a", audio_br])
            elif a_codec == "Copy":
                cmd.extend(["-c:a", "copy"])
            if a_codec != "Copy":
                mixdown = s.get("audio_mixdown", "Auto")
                if mixdown != "Auto":
                    cmd.extend(["-ac", {"Mono": "1", "Stereo": "2", "5.1": "6", "7.1": "8"}.get(mixdown)])
            if af_list and a_codec != "Copy":
                cmd.extend(["-af", ",".join(af_list)])

        if vf_list:
            cmd.extend(["-vf", ",".join(vf_list)])

        depth_str = s.get("bit_depth", "10-bit")
        pix_fmt = "yuv420p12le" if "12-bit" in depth_str else "yuv420p" if "8-bit" in depth_str else "yuv420p10le"
        encoder = s.get("encoder", "SVT-AV1")
        crf_val = f"{crf_override:g}" if isinstance(crf_override, (float, int)) else str(
            crf_override or s.get("crf", "34.0")
        )
        gop_frames = getattr(self, "current_gop_frames", None)

        # PLUGIN HOOK: video-codec flags come from the active EncoderBackend plugin.
        backend = backend_for_encoder_setting(encoder)
        cmd.extend(backend.ffmpeg_video_args(s, crf_val, pix_fmt, gop_frames, is_sample, output_file))

        if not is_sample and apply_trim and target_duration > 0:
            cmd.extend(["-t", str(target_duration)])
        if output_file.endswith(".incomplete"):
            cmd.extend(["-f", "mp4" if s.get("out_format") == "MP4" else "matroska"])
        cmd.append(output_file)
        return cmd
