"""Sample-chunk and frame extraction used by the quality preview."""
import subprocess

import core.config as config
from core.video_info import format_fps


class MediaExtractMixin:
    def extract_video_chunk(
        self,
        input_file,
        output_file,
        seek_point,
        duration,
        fps,
        pix_fmt,
        vf_string=None,
        use_ffv1=False,
        exact_cfr=False
    ):
        cmd = [config.FFMPEG_EXE, "-y", "-ss", str(seek_point)]
        if exact_cfr:
            cmd.extend(["-t", str(duration)])
        cmd.extend(["-i", input_file])

        if use_ffv1:
            vfs = []
            if exact_cfr:
                vfs.append(f"fps=fps={format_fps(fps)}")
            if vf_string:
                vfs.append(vf_string)
            if exact_cfr:
                vfs.append("setpts=PTS-STARTPTS")
            cmd.extend(
                [
                    "-vf",
                    ",".join(vfs) if vfs else "null",
                    "-c:v",
                    "ffv1",
                    "-level",
                    "3",
                    "-pix_fmt",
                    pix_fmt,
                    "-an",
                    "-sn",
                    output_file
                ]
            )
        else:
            cmd.extend(["-frames:v", str(int(duration * fps)), "-c:v", "copy", "-an", "-sn", output_file])

        res = self._run_tracked(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
        if self.cancel_requested:
            raise Exception("Cancelled")
        if res.returncode != 0:
            self._record_failure("Sample extraction (FFmpeg)", cmd, res.returncode, res.stderr, console_tail=15)
            raise RuntimeError(f"FFmpeg chunk extraction failed with exit code {res.returncode}")

    def extract_frame_as_webp(self, input_file, output_file, frame_idx):
        self._run_tracked(
            [
                config.FFMPEG_EXE,
                "-y",
                "-i",
                input_file,
                "-vf",
                f"select='eq(n,{frame_idx})'",
                "-vframes",
                "1",
                "-c:v",
                "libwebp",
                "-lossless",
                "1",
                output_file
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def extract_merged_frame_as_webp(self, orig_file, enc_file, output_file, frame_idx):
        fc = f"select='eq(n,{frame_idx})',format=yuv420p"
        self._run_tracked(
            [
                config.FFMPEG_EXE,
                "-y",
                "-i",
                orig_file,
                "-i",
                enc_file,
                "-filter_complex",
                f"[0:v]{fc}[orig];[1:v]{fc}[enc];[orig][enc]scale2ref=w=iw:h=ih[orig_scaled][enc];[orig_scaled][enc]hstack",
                "-vframes",
                "1",
                "-c:v",
                "libwebp",
                "-lossless",
                "1",
                output_file
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
