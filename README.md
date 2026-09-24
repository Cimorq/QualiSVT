# QualiSVT

**QualiSVT** is a modular Windows-oriented video encoding and quality-assessment application focused on **SVT-AV1**, with support for **x265**, **FFmpeg**, **HandBrakeCLI**, automated quality testing, Auto-CRF search, sample-based evaluation, caching, queue/resume, and hardware telemetry.

The project is designed around one main idea:

> Do not choose a CRF only by file size. Measure visual quality, estimate the final size from representative samples, and select the highest CRF that satisfies the user's quality and size constraints.

---

## 1. Main Features

- SVT-AV1 encoding through FFmpeg's `libsvtav1`.
- x265 encoding through FFmpeg.
- Optional HandBrakeCLI backend for SVT-AV1/x265.
- Experimental/extension backend for direct `SvtAv1EncApp`.
- Auto-CRF search using:
  - VMAF
  - xPSNR
  - SSIMULACRA2
  - Butteraugli
  - CVVDP
- Pre-encode quality analysis.
- Post-encode quality verification.
- Sample-based evaluation to avoid encoding the whole movie during CRF search.
- AB-AV1-compatible output-size prediction for Auto-CRF.
- SQLite result cache.
- Queue processing and resumable/incomplete output handling.
- Pause/cancel support for tracked processes.
- Optional CPU/RAM/power telemetry through OpenHardwareMonitor + PSUtil.
- Automatic GOP calculation, cropping, scaling, deinterlacing and custom FFmpeg filters.
- Audio encoding with Opus/AAC or audio copy.
- Subtitle preservation where the selected container supports it.
- Portable or AppData configuration/cache storage.

---

# 2. Architecture

```text
QualiSVT/
│
├─ main.py
│
├─ core/
│  ├─ config.py             Global paths, tools, feature flags and cache version
│  ├─ cache_db.py           SQLite cache + file/tool fingerprints
│  ├─ video_info.py         FFprobe-based media information
│  ├─ vmaf_utils.py         Metric parsing and quality labels
│  ├─ proc_utils.py         Process-tree control
│  ├─ output_capture.py     Failure/output capture
│  └─ fs_utils.py           Small filesystem helpers
│
├─ plugins/
│  ├─ base.py               Plugin interfaces/registry
│  ├─ encoder_ffmpeg.py     FFmpeg/libsvtav1 and x265 backends
│  ├─ encoder_handbrake.py  HandBrakeCLI backend
│  ├─ encoder_svtav1encapp.py
│  │                         Direct SvtAv1EncApp pipeline definition
│  └─ monitor_ohm.py        OpenHardwareMonitor telemetry plugin
│
├─ gui/
│  ├─ encoder_app.py        Main application/state object
│  └─ mixins/
│     ├─ ui_builder.py
│     ├─ file_list.py
│     ├─ process_control.py
│     ├─ encoding_control.py
│     ├─ command_builder.py
│     ├─ execution.py
│     ├─ media_prep.py
│     ├─ media_extract.py
│     ├─ quality_eval.py
│     ├─ queue_runner.py
│     └─ logging_mixin.py
│
├─ Bin/                     Optional bundled executables
└─ requirements.txt
```

The application follows a **mixin + plugin** architecture. GUI concerns are separated from media analysis, encoding backends, metrics, caching and system monitoring.

---

# 3. Requirements

Required:

- Python 3.x
- FFmpeg
- FFprobe
- FFVship

Optional:

- HandBrakeCLI — required only when the HandBrake backend is used.
- SvtAv1EncApp — required only for the direct SVT-AV1 backend.
- `psutil` — enables CPU/RAM monitoring.
- `tkinterdnd2` — enables drag-and-drop support.
- OpenHardwareMonitor — enables CPU package power sampling on supported Windows setups.

Executables can be placed inside `Bin/` or available through `PATH`.

Run:

```bash
pip install -r requirements.txt
python main.py
```

---

# 4. Encoding Pipeline

For a normal FFmpeg encode, QualiSVT builds a command approximately like:

```text
Input
  ↓
FFmpeg demux/decode
  ↓
optional deinterlace / crop / scale / custom filters
  ↓
SVT-AV1 or x265
  ↓
optional audio encode/copy
  ↓
subtitle mapping
  ↓
MKV or MP4
```

For Auto-CRF, the pipeline is deliberately different:

```text
Full source
   ↓
Sample extraction
   ↓
Short sample(s)
   ↓
Encode sample at CRF N
   ↓
VMAF / xPSNR / FFVship metric
   ↓
Estimate final output size
   ↓
Accept or reject CRF
   ↓
Choose next CRF
   ↓
Repeat
```

Only after the Auto-CRF search finishes does the normal full encode run.

---

# 5. Sampling System

Sampling is used by Auto-CRF and by the pre/post quality tests.

Settings include:

- `Sample Duration` — length of each sample in seconds.
- `Samples Count` — explicit number of samples. `0` means automatic.
- `Sample-Every` — automatic spacing interval in minutes when count is `0`.
- Sample storage location.
- Optional retention of extracted samples.

### Automatic sample count

If `Samples Count = 0`, the number of samples is approximately:

```text
ceil(target_duration / sample_interval)
```

For example:

```text
Movie = 87 minutes
Sample interval = 12 minutes

ceil(87 / 12) = 8 samples
```

With a 20-second sample duration, QualiSVT then distributes the samples across the target range rather than simply taking the first 8 segments.

### Even distribution

For `N` samples of duration `D` within a target duration `T`, the program calculates a gap and places the samples approximately like:

```text
[gap] [sample] [gap] [sample] ... [gap]
```

This gives the search exposure to different scenes instead of concentrating all measurements at the beginning.

---

# 6. Sample Extraction

Two modes exist:

### Normal sample extraction

When the selected quality metric does not require FFVship reference processing, the source sample is extracted without re-encoding the video stream:

```text
ffmpeg -ss START -i INPUT -frames:v N -c:v copy -an -sn SAMPLE.mkv
```

### FFVship reference extraction

For metrics such as SSIMULACRA2, Butteraugli and CVVDP, the reference is normalized through FFV1 when required. This provides a stable, frame-accurate reference representation.

Typical path:

```text
Input
 ↓
FPS normalization
 ↓
optional reference filters
 ↓
FFV1 lossless reference sample
```

The target metric is then calculated against this reference.

---

# 7. Supported Quality Metrics

## VMAF

VMAF is calculated with FFmpeg's `libvmaf` filter using:

```text
model = vmaf_v0.6.1
pool  = Mean
```

The final score is the pooled mean VMAF score.

**Higher is better.**

Default quality labels:

```text
97+   Visually Lossless
93+   Excellent
85+   Good
75+   Fair
60+   Poor
<60   Bad
```

### Example

```text
VMAF = 97.01
→ Visually Lossless
```

---

## xPSNR

xPSNR is calculated through FFmpeg's `xpsnr` filter.

The project combines Y/U/V as:

```text
(Y × 4 + U + V) / 6
```

This intentionally gives the luma channel four times the weight of each chroma plane.

**Higher is better.**

Current labels are based on PSNR-like thresholds:

```text
45+   Visually Lossless
42+   Excellent
38+   Good
35+   Fair
30+   Poor
<30   Bad
```

---

## SSIMULACRA2

SSIMULACRA2 is executed through FFVship.

**Higher is better.**

Current labels:

```text
90+   Visually Lossless
75+   Excellent
60+   Good
50+   Fair
30+   Poor
<30   Bad
```

---

## CVVDP

CVVDP is executed through FFVship.

**Higher is better.**

Current labels:

```text
9.5+  Visually Lossless
8.5+  Excellent
7.5+  Good
6.5+  Fair
5.5+  Poor
<5.5  Bad
```

---

## Butteraugli

Butteraugli is also executed through FFVship.

Unlike the other metrics, **lower is better**.

Current labels:

```text
≤0.5  Visually Lossless
≤1.5  Excellent
≤2.5  Good
≤3.5  Fair
≤4.5  Poor
>4.5  Bad
```

This inversion is important in Auto-CRF because the search uses:

```text
score <= target
```

instead of:

```text
score >= target
```

---

# 8. Auto-CRF Search

Auto-CRF is the main intelligent part of QualiSVT.

The user defines:

```text
Metric             VMAF
Target Quality     97.0
Minimum CRF        20.0
Maximum CRF        40.0
Maximum Size       30%
```

The goal is:

> Find the highest CRF value that satisfies BOTH the quality target and the maximum-size constraint.

Higher CRF normally means smaller output and lower quality, while lower CRF normally means larger output and higher quality.

---

# 9. CRF Search Grid

The search uses a CRF step of **0.5**.

Therefore:

```text
20.0
20.5
21.0
21.5
...
37.0
37.5
38.0
...
40.0
```

Internally the algorithm converts CRF to half-step integer coordinates:

```text
CRF 37.0 → q = 74
CRF 37.5 → q = 75
```

This makes interpolation predictable.

---

# 10. Initial CRF Selection

The initial test is approximately the midpoint between minimum and maximum CRF.

Example:

```text
min = 20
max = 40

initial q = midpoint
initial CRF ≈ 30
```

This explains why an Auto-CRF log often begins with:

```text
CRF 30
```

---

# 11. Search Direction

Suppose:

```text
CRF 30
VMAF = 98.89
Size = 49.8%
```

Quality is good, but size is too large. The program therefore moves toward a higher CRF.

Then:

```text
CRF 40
VMAF = 95.91
Size = 23.7%
```

Now the size is good, but VMAF is too low.

Therefore the desired CRF must be somewhere between 30 and 40.

---

# 12. Quality-Based Interpolation

Instead of checking every half-step blindly, QualiSVT can interpolate between a known-good-quality point and a known-bad-quality point.

Conceptually:

```text
CRF_low  → quality above target
CRF_high → quality below target
```

The program estimates where the target quality lies between them.

Example:

```text
CRF 36.5 → VMAF 97.18
CRF 40.0 → VMAF 95.91
Target    → VMAF 97.00
```

The interpolated value points the search toward approximately CRF 37.

The result is rounded to the half-step grid.

---

# 13. AB-AV1-Compatible Size Prediction

This is the most important part of the recent QualiSVT change.

For each CRF test, the program encodes only the short samples. It then estimates what the full encode would be.

Two estimates are calculated.

## Method A — Duration-based estimate

```text
Duration Estimate =
    (Encoded Sample Bytes / Total Sample Duration)
    × Full Target Duration
```

Example:

```text
Encoded samples = 12 MiB
Total sample duration = 60 s
Full video duration = 900 s

12 / 60 × 900 = 180 MiB
```

So the duration-based prediction is 180 MiB.

---

## Method B — File-percent estimate

The sample's encoded size is compared with the corresponding source sample size.

```text
File-Percent Estimate =
    Full Input File Size
    × (Encoded Sample Bytes / Source Sample Bytes)
```

Example:

```text
Input file = 1,000 MiB
Source samples = 100 MiB
Encoded samples = 4 MiB

1,000 × (4 / 100) = 40 MiB
```

So this estimate is 40 MiB.

---

## Final predicted size

QualiSVT uses the smaller of the two:

```text
Predicted Size = min(Duration Estimate, File-Percent Estimate)
```

Example:

```text
Duration Estimate    = 180 MiB
File-Percent Estimate = 40 MiB

Predicted Size = 40 MiB
```

The size constraint is then evaluated against the **full original input file size**:

```text
Predicted Percent =
    Predicted Size / Full Input Size × 100
```

Example:

```text
40 / 1,000 × 100 = 4%
```

This is the percentage shown by Auto-CRF.

---

# 14. Why the Encoded Size Uses Video Stream Bytes

During Auto-CRF, samples are encoded without audio:

```text
with_audio = false
```

Therefore container overhead, audio and other non-video streams should not be allowed to distort the encoded-size measurement.

The encoded sample size is read from the first video stream using FFprobe.

If FFprobe does not provide a usable `stream=size` value, QualiSVT falls back to summing the packet sizes for the first video stream.

This is important for some MKV files where FFprobe reports `N/A` for stream size.

---

# 15. Auto-CRF Acceptance Rules

For metrics where higher is better:

```text
quality_ok = score >= target
```

For Butteraugli:

```text
quality_ok = score <= target
```

The size condition is:

```text
size_ok = predicted_percent <= max_percent
```

Both must be true:

```text
size_ok AND quality_ok
```

Example:

```text
Target VMAF = 97
Max Size    = 30%

CRF 37
VMAF = 97.01
Size = 29.9%

97.01 >= 97    → TRUE
29.9 <= 30     → TRUE

Result → Accepted
```

---

# 16. Real Example

Given:

```text
Input: 1.mp4
Duration: 87 seconds
Input size: 126.9 MB
Target metric: VMAF
Target VMAF: 97.0
Max size: 30%
Preset: 6
Bit depth: 10-bit
```

The search can produce:

```text
CRF 30
VMAF 98.89
Size 49.8%
→ Rejected: Size

CRF 40
VMAF 95.91
Size 23.7%
→ Rejected: VMAF

CRF 36.5
VMAF 97.18
Size 31.0%
→ Rejected: Size

CRF 37
VMAF 97.01
Size 29.9%
→ Accepted
```

The selected CRF is therefore:

```text
CRF 37
```

Predicted size:

```text
126.9 MB × 29.9% ≈ 37.9 MB
```

---

# 17. Pre-Encode Quality Test

The pre-test answers:

> If I encode representative sections at the selected CRF, what quality should I expect and what full-file size/time should I expect?

The workflow is:

```text
Input
 ↓
Extract samples
 ↓
Encode samples with the selected CRF
 ↓
Calculate selected metrics
 ↓
Average metric scores
 ↓
Extrapolate size from sample duration
 ↓
Extrapolate encode time from sample duration
```

Unlike Auto-CRF, the pre-test's displayed size prediction is based on the sample encode rate over duration.

---

# 18. Post-Encode Quality Test

Post-test compares the actual final encode against the source.

For each selected sample:

```text
Original sample → lossless reference
Encoded sample  → analysis sample
                  ↓
             quality metric
```

It can report:

- Average score.
- Worst frame.
- Middle frame.
- Best frame.

For samples with at least 20 frame scores, the most extreme 5% at each end are excluded before selecting representative worst/mid/best frames.

For Butteraugli, lower values are better, so the meaning of worst/best is inverted accordingly.

---

# 19. Frame Preview

When samples are kept, QualiSVT can generate WebP frame previews.

The frame extraction system can produce:

- Original frame.
- Encoded frame.
- Merged side-by-side comparison.

This is useful when a numerical metric alone does not explain a visible artifact.

---

# 20. Encoding Settings

## Video Encoder

Current UI/backends support:

```text
SVT-AV1
x265
```

through FFmpeg, with HandBrakeCLI available as an alternate engine.

## Bit depth

The application maps the selected depth to:

```text
8-bit  → yuv420p
10-bit → yuv420p10le
12-bit → yuv420p12le
```

## SVT-AV1 parameters

The UI passes custom SVT parameters in FFmpeg form, for example:

```text
tune=0:scd=1:hbd-mds=1:ac-bias=2:enable-variance-boost=1:complex-hvs=1
```

and the backend adds them to the FFmpeg command using `-svtav1-params`.

## GOP

QualiSVT can calculate or apply a GOP/keyint value. The resolved keyint is included in the cache key so that changing GOP settings does not reuse incompatible analysis.

---

# 21. Audio Processing

Normal encodes can use:

- Opus
- AAC
- Copy

Default example:

```text
Codec: OPUS
Bitrate: 128K
```

Auto-CRF sample encoding deliberately disables audio:

```text
-an
```

because audio quality is not relevant to the video CRF decision.

---

# 22. Subtitle Handling

Subtitles are handled independently of audio.

For MKV, text subtitles can normally be copied.

For MP4, supported text subtitle codecs are mapped to `mov_text` where possible.

This separation prevents the absence of audio from accidentally removing subtitles during the main encode.

---

# 23. Scaling, Cropping and Filters

The encoder can apply:

- Original resolution.
- 480p.
- 720p.
- 1080p.
- 1440p.
- 4K.
- Automatic crop.
- Deinterlacing (`yadif`).
- Custom video filters.
- Custom audio filters (FFmpeg backend).

The exact resolved filters are included in Auto-CRF cache settings because a change in filters changes the encoded pixels and invalidates old quality results.

---

# 24. Range Modes

The queue system can operate on:

- Full Video.
- A user-defined time range.
- Frame-based or chapter-based ranges where supported by the UI/media information.

The selected start offset and target duration are part of the analysis/cache identity.

---

# 25. Caching System

QualiSVT uses SQLite to avoid repeating expensive analyses.

A cache key includes:

- Input-file fingerprint.
- FFmpeg/FFVship/HandBrakeCLI tool fingerprint.
- Encoder.
- Preset.
- Tune.
- CRF.
- Encoder parameters.
- Bit depth.
- GOP settings.
- Scaling/cropping.
- Deinterlacing.
- Sample configuration.
- Seek offset and target duration.
- Exact video/reference filter chains.
- Cache schema.
- Size-estimation mode.

### Input fingerprint

The file fingerprint uses:

```text
file size
+ nanosecond modification time
+ first 1 MiB
+ last 1 MiB
```

This gives a fast way to detect changed input files without hashing the entire movie.

### When to disable cache

Disable `Use Cache` when:

- You replaced FFmpeg/FFVship with another build.
- You changed a dependency manually.
- You want a clean experiment.
- You changed implementation code affecting analysis.

---

# 26. Cache Schema

`core/config.py` currently defines:

```python
CACHE_SCHEMA_VERSION = 3
```

The version is included in the cache settings fingerprint so that code changes can invalidate previous analysis where necessary.

---

# 27. Queue and Resume Logic

The main queue processes files sequentially.

For full encodes, output files can be temporarily written as:

```text
filename.incomplete
filename.001.incomplete
filename.002.incomplete
```

If a previous run was interrupted, QualiSVT checks the readable duration and can resume or restore a completed output instead of encoding the entire file again.

This is especially useful for long SVT-AV1 encodes.

---

# 28. Pause / Cancel

The process-control layer tracks external encoder processes and their child processes.

The application can:

- Stop a job.
- Pause/resume where supported by the process-control implementation.
- Cancel an Auto-CRF search.
- Cancel FFVship analysis.
- Clean up temporary files after cancellation.

---

# 29. Hardware Monitoring

The monitoring plugin can collect:

```text
Average CPU usage
Peak RAM usage
Average CPU package power
Estimated energy usage (Wh)
```

CPU/RAM monitoring uses `psutil` when available.

On supported Windows systems, the OpenHardwareMonitor plugin can read CPU package power through WMI/CIM using PowerShell.

Example report:

```text
Avg CPU Power = 165 W
Duration      = 1.5 h

Estimated energy ≈ 165 × 1.5 = 247.5 Wh
```

---

# 30. Plugin System

The project contains two main plugin interfaces.

## EncoderBackend

An encoder backend defines how a selected encoder produces output.

Current backends include:

```text
FFmpeg
HandBrakeCLI
SvtAv1EncApp definition/pipeline
```

The direct SvtAv1EncApp backend builds a three-stage pipeline:

```text
FFmpeg decode/filter
        ↓ Y4M
SvtAv1EncApp
        ↓ IVF
FFmpeg mux
        ↓
Final container
```

The direct SvtAv1EncApp plugin is currently a pipeline definition/extension point rather than the normal execution path of the application.

## SystemMonitorPlugin

A monitoring backend provides telemetry to the main application without hard-coding the sensor implementation into the GUI.

Current implementation:

```text
OpenHardwareMonitor
```

The architecture can later support LibreHardwareMonitor, NVML, Linux hwmon, etc.

---

# 31. Error Handling

External process failures are captured with:

- Command line.
- Return code.
- Process output tail for the UI.
- More complete output in job logs.

Fatal startup errors are written to:

```text
QualiSVT_crash_log.txt
```

when the application can create the log.

---

# 32. Why QualiSVT Uses Samples

Encoding a complete movie for every candidate CRF would be extremely expensive.

For example, testing:

```text
CRF 30
CRF 40
CRF 36.5
CRF 37
```

on a 2-hour movie would require multiple full encodes.

Instead, QualiSVT might use:

```text
8 × 20-second samples
= 160 seconds of encoded material
```

The samples are then used to estimate:

- Visual quality.
- Approximate final size.
- Approximate full-encode time.

This dramatically reduces search cost while preserving scene diversity.

---

# 33. Important Limitation of Sample-Based Prediction

Sample estimation is an estimate, not a guarantee.

A full encode can differ from the estimate because:

- The samples may not contain every difficult scene.
- Scene complexity can vary significantly.
- Encoder decisions can change with surrounding context.
- Audio/container overhead is not represented in the Auto-CRF sample encode.
- Very short or highly unusual videos can make extrapolation noisy.

Therefore the safest interpretation is:

```text
Auto-CRF = statistically informed search
not an exact full-file size calculator
```

The post-encode test exists to verify the final result.

---

# 34. Recommended Example Configuration

For a quality-first SVT-AV1 workflow:

```text
Engine            = FFmpeg
Encoder           = SVT-AV1
Preset            = 6
Bit Depth         = 10-bit
Metric            = VMAF
Target VMAF       = 97.0
Max Output        = 30%
CRF Min           = 20
CRF Max           = 40
Sample Duration   = 20 s
Samples Count     = 0
Sample Interval   = 12 min
```

For the example file discussed during development, this produced:

```text
CRF 37
VMAF 97.01
Estimated Size 29.9%
```

and the search accepted CRF 37.

---

# 35. Example SVT-AV1 Parameters

A test configuration can use:

```text
tune=0:scd=1:hbd-mds=1:ac-bias=2:enable-variance-boost=1:complex-hvs=1
```

Together with:

```text
Preset = 6
Bit depth = 10-bit
Pixel format = yuv420p10le
```

The exact result depends on the SVT-AV1 build, input source, filters, sample placement and FFmpeg build.

---

# 36. Interpreting an Auto-CRF Log

Example:

```text
CRF 30
VMAF 98.89
Size 49.8%
Rejected: Size
```

Meaning:

- Quality target was reached.
- Size target was not reached.
- Increase CRF.

Next:

```text
CRF 40
VMAF 95.91
Size 23.7%
Rejected: VMAF
```

Meaning:

- Size target was reached.
- Quality target was missed.
- Decrease CRF.

Next:

```text
CRF 37
VMAF 97.01
Size 29.9%
Target Met!
```

Meaning:

- Both constraints are satisfied.
- CRF 37 becomes the accepted result.

---

# 37. Project Philosophy

QualiSVT is not intended to replace subjective viewing.

Its workflow is:

```text
Encode less
Measure more
Search intelligently
Verify the final encode
```

A numerical score is evidence, not a substitute for visual inspection.

For difficult material, the recommended workflow is:

```text
Auto-CRF
   ↓
Pre-test
   ↓
Full encode
   ↓
Post-test
   ↓
Inspect representative worst/best frames
```

---

# 38. Current Status

The main FFmpeg-based SVT-AV1 path is the primary encoding route.

The architecture deliberately leaves extension points for:

- Additional encoder backends.
- Additional quality metrics.
- New hardware-monitor sources.
- Alternative sample strategies.
- Future direct SvtAv1EncApp execution support.

---

# 39. License / Attribution

Add the project's license and third-party attribution information here if/when the repository establishes a formal license file.

Relevant external projects/tools include FFmpeg, SVT-AV1, FFVship, HandBrakeCLI, VMAF and OpenHardwareMonitor.

