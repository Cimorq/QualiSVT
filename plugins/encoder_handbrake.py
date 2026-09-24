"""
HandBrakeCLI encoder backend.

Unlike ffmpeg's libsvtav1/libx265 (which only need extra *flags* spliced into
an ffmpeg command someone else builds), HandBrakeCLI is a wholly separate,
self-contained tool: it does its own demuxing, filtering, audio/subtitle
handling and encoding in one process. So it can't share the narrow
"ffmpeg_video_args" seam — it owns its *entire* command line.

This is exactly the logic that used to live inline in
EncoderApp.get_handbrake_cmd(); moved here verbatim, parameterized so it no
longer reaches into `self` for current_gop_frames (passed in explicitly).
"""
import shlex

import core.config as config
from plugins.base import EncoderBackend, manager


@manager.register_encoder
class HandBrakeBackend(EncoderBackend):
    name = "HandBrakeCLI"
    is_ffmpeg_native = False  # owns its whole command line; see build_command()

    @classmethod
    def is_available(cls) -> bool:
        return bool(config.HANDBRAKE_EXE)

    def build_command(self, input_file, output_file, s, gop_frames=None,
                       encode_duration=0, seek_start=0.0, apply_trim=False,
                       with_audio=True, crf_override=None):
        cmd = [config.HANDBRAKE_EXE, "-i", input_file, "-o", output_file, "--verbose", "0",
               "-f", "av_mp4" if s.get("out_format", "MKV") == "MP4" else "av_mkv"]

        enc, depth = s.get("encoder", "SVT-AV1"), s.get("bit_depth", "10-bit")
        hb_enc = ("svt_av1_10bit" if "10-bit" in depth else "svt_av1") if enc == "SVT-AV1" else (
            "x265_10bit" if "10-bit" in depth else "x265_12bit" if "12-bit" in depth else "x265"
        )
        cmd.extend(
            [
                "-e",
                hb_enc,
                "-q",
                f"{crf_override:g}" if isinstance(crf_override, (float, int)) else str(
                    crf_override or s.get("crf", "34.0")
                )
            ]
        )

        if preset := s.get("preset", ""):
            cmd.extend(["--encoder-preset", preset])
        tune = s.get("tune", "")
        if tune and tune.lower() != "none":
            cmd.extend(
                [
                    "--encoder-tune",
                    tune.split("(")[1].replace(")", "").strip() if enc == "SVT-AV1" and "(" in tune else tune
                ]
            )

        params = s.get("svt_params", "") if enc == "SVT-AV1" else s.get("x265_params", "")
        if gop_frames is not None:
            params = (params.strip() + f":keyint={gop_frames}") if params.strip() else f"keyint={gop_frames}"
        if params.strip():
            cmd.extend(["-x", params.strip()])

        if not with_audio:
            cmd.extend(["-a", "none"])
        else:
            cmd.extend(["-a", "1"] if s.get("audio_track", "") == "Track 1 (Default)" else ["--all-audio"])
            codec, audio_br = s.get("audio_codec", "OPUS"), s.get("audio_br", "128K")
            if codec == "OPUS":
                cmd.extend(["-E", "opus", "-B", audio_br.replace("K", "")])
            elif codec == "AAC":
                cmd.extend(["-E", "av_aac", "-B", audio_br.replace("K", "")])
            elif codec == "Copy":
                cmd.extend(["-E", "copy", "--audio-fallback", "av_aac"])
            if codec != "Copy":
                mix = s.get("audio_mixdown", "Auto")
                if mix != "Auto":
                    cmd.extend(
                        ["-6", {"Mono": "mono", "Stereo": "stereo", "5.1": "5point1", "7.1": "7point1"}.get(mix)]
                    )

        if s.get("deint", False):
            cmd.extend(["--deinterlace", "yadif"])
        cmd.extend(["--crop-mode", "auto" if s.get("auto_crop", False) else "none"])

        scale = s.get("scale", "Original")
        if scale != "Original":
            mapping = {
                "4K": ("3840", "2160"),
                "1440p": ("2560", "1440"),
                "1080p": ("1920", "1080"),
                "720p": ("1280", "720"),
                "480p": ("854", "480")
            }
            w, h = next((v for k, v in mapping.items() if k in scale), ("1920", "1080"))
            cmd.extend(["--maxWidth", w, "--maxHeight", h])

        if custom_hb := s.get("custom_hb", "").strip():
            try:
                cmd.extend(shlex.split(custom_hb))
            except ValueError:
                pass

        if s.get("out_format", "MKV") != "MP4" and not output_file.lower().endswith(".mp4"):
            cmd.extend(["--all-subtitles"])

        if apply_trim or seek_start > 0:
            if s.get("range_mode", "Full Video") == "Chapters":
                cmd.extend(["-c", f"{s.get('range_start', '').strip() or '1'}-{s.get('range_end', '').strip() or '1'}"])
            else:
                if seek_start > 0:
                    cmd.extend(["--start-at", f"seconds:{seek_start:.3f}"])
                if apply_trim and encode_duration > 0:
                    cmd.extend(["--stop-at", f"seconds:{encode_duration:.3f}"])

        return cmd
