# 🎬 QualiSVT v1.0.2
**Advanced Video Encoder & Quality Assessment Tool**

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

**QualiSVT** is an advanced, versatile, and GUI-based batch video encoder and precise quality assessment tool. It is specifically optimized for custom **SVT-AV1** builds (such as SVT-AV1-HDR and HandBrake-SVT-AV1-Tritium), while also fully supporting x265 (HEVC).

With QualiSVT, you don't just encode videos; you leverage smart algorithms to find the absolute best settings, monitor your hardware resources (including CPU power consumption), and evaluate outputs using the world's most advanced image quality metrics (VMAF, SSIMULACRA2, CVVDP).

---

## ✨ Key Features

### ⚙️ Advanced Rendering & Encoding Engines
* **Dual Engine Support:** Seamlessly switch between `FFmpeg` and `HandBrakeCLI`.
* **Deep Encoder Control:** Full support for SVT-AV1 and x265 with selectable color depths (8-bit, 10-bit, 12-bit).
* **Smart GOP Management:** Set GOP manually, by seconds (e.g., 10-Second GOP), or rely on encoder defaults.
* **Custom Parameters:** Dedicated fields for injecting custom `svtav1-params` and `x265-params`.

### 🧠 Smart Auto-CRF Search
* **Find the Ideal CRF:** Inspired by `AB-AV1`, QualiSVT uses chunk sampling, binary search, and interpolation to automatically find the highest CRF that meets a specific "Target Quality Score".
* **Size Constraints:** Set a hard limit so the output file never exceeds a certain percentage of the original size (e.g., max 30%).

### 📊 Quality Estimation & Validation
* **State-of-the-Art Metrics:** Built-in support for VMAF, xPSNR, SSIMULACRA2, Butteraugli, and CVVDP (accelerated via GPU using FFVship).
* **Pre-Encode Estimate:** Samples different segments of your video to estimate final file size, encoding time, and quality score *before* running the full encode.
* **Visual Comparison:** Automatically extracts the "Worst, Mid, and Best" frames post-encode as lossless `WebP` images. It can even merge the original and encoded frames side-by-side for pixel-peeping.

### ⚡ Resource Monitoring & Power Tracking
* **Real-Time CPU & RAM Tracking:** Live monitoring of CPU utilization and RAM usage per process (powered by `psutil`).
* **Power & Energy Calculation:** If **Open Hardware Monitor (OHM)** is running in the background, QualiSVT automatically connects to CPU package sensors via WMI/PowerShell to log **Average Power (W)** and **Total Energy Consumed (Wh)** for every encode!
* **Process Management:** Easily set process Priority and CPU Core limits (Affinity).

### 🛠 Video Tools & Smart Filters
* **Smart Auto Crop:** Automatically detects and removes black bars (`cropdetect`).
* **Precision Trimming:** Extract specific parts of a video by seconds, timecodes (hh:mm:ss), frames, or **Chapters**.
* **Quick Filters:** Instant standard scaling (1080p, 4K, etc.), Deinterlacing (Yadif), and Auto Fade In/Out effects.
* **Custom CLI Commands:** Add custom `vf` (Video) and `af` (Audio) filters for FFmpeg, or extra CLI arguments for HandBrake.

### 🛡 Resilience & Smart Caching
* **Bulletproof Auto-Resume:** If your PC crashes or loses power, QualiSVT will detect the incomplete file on the next run, resume encoding exactly where it left off, and seamlessly concatenate the parts.
* **Intelligent Caching:** Estimates, Auto-CRF searches, and quality scores are hashed and saved to `QualiSVT_Cache.json`. If you process the same video with the same settings later, results load from the cache instantly!

---

## 💻 Prerequisites & Dependencies

To ensure all features work perfectly, make sure the following requirements are met:

### 1. OS & Python
* **Windows 10 or 11** (Hardware monitoring and WMI features are optimized for Windows).
* **Python 3.8** or newer.
* Required Python libraries:
  ```bash
  pip install psutil tkinterdnd2
  ```
  *(Note: `psutil` is required for resource management, and `tkinterdnd2` enables Drag & Drop functionality in the GUI).*

### 2. External Binaries & Tools
For the application to function, the following standalone `.exe` files must either be added to your system's `PATH` environment variable, **OR** placed in a folder named `Bin` directly next to the `QualiSVT-1.0.4.py` script.

* **FFmpeg & FFprobe**: The core processing and encoding engine.
  * *Recommended Build:* [FFmpeg-Builds-SVT-AV1-HDR](https://github.com/QuickFatHedgehog/FFmpeg-Builds-SVT-AV1-HDR/releases/latest)
* **HandBrakeCLI**: Required only if you plan to use the HandBrake engine instead of FFmpeg.
  * *Recommended Build:* [HandBrake-SVT-AV1-Tritium](https://github.com/Uranite/HandBrake-SVT-AV1-Tritium/releases)
* **FFVship**: Required for ultra-fast GPU calculation of advanced metrics (SSIMULACRA2, Butteraugli, CVVDP).
  * *Download Link:* [FFVship Releases](https://codeberg.org/Line-fr/Vship/releases)

### 3. Power Tracking Setup (Open Hardware Monitor)
For QualiSVT to calculate Watts (W) and Total Energy (Wh) during encoding:
* **[Open Hardware Monitor](https://openhardwaremonitor.org/)** must be downloaded and **running in the background** *before* you start the encoding process. QualiSVT will automatically hook into its telemetry to calculate your power consumption.

---

## 🚀 Installation & Usage

1. Clone the repository:
   ```bash
   git clone https://github.com/Cimorq/QualiSVT.git
   cd QualiSVT
   ```
2. Install the required Python dependencies:
   ```bash
   pip install psutil tkinterdnd2
   ```
3. Create a directory named `Bin` in the root folder and place your downloaded executables inside (`ffmpeg.exe`, `ffprobe.exe`, `FFVship.exe`, `HandBrakeCLI.exe`).
4. Run the application:
   ```bash
   python QualiSVT-1.0.2.py
   ```
5. Drag and drop your video files into the queue, select your target metrics in the **Quality Metrics** tab, and hit **Start Encoding**.

---

## 👨‍💻 Author & Credits

* **Author:** Simorq - [Cimorq GitHub](https://github.com/Cimorq)
* **Powered by these amazing open-source projects:** 
  * [FFVship](https://codeberg.org/Line-fr/Vship)
  * [Open Hardware Monitor](https://github.com/HardwareMonitor/openhardwaremonitor)
  * [FFmpeg-Builds-SVT-AV1-HDR](https://github.com/QuickFatHedgehog/FFmpeg-Builds-SVT-AV1-HDR)
  * [HandBrake-SVT-AV1-Tritium](https://github.com/Uranite/HandBrake-SVT-AV1-Tritium)
```
