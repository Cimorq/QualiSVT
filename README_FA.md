# QualiSVT — مستندات فارسی

**QualiSVT** یک برنامه‌ی ماژولار برای **انکود و ارزیابی کیفیت ویدئو** است که تمرکز اصلی آن روی **SVT-AV1** است و از x265، FFmpeg، HandBrakeCLI، جست‌وجوی Auto-CRF، سنجش کیفیت، نمونه‌برداری، کش، صف پردازش، ادامه‌ی انکود و مانیتورینگ سخت‌افزار پشتیبانی می‌کند.

ایده‌ی اصلی پروژه این است:

> CRF را فقط بر اساس حجم فایل انتخاب نکن؛ کیفیت بصری را اندازه بگیر، حجم نهایی را با نمونه‌های نماینده تخمین بزن و بالاترین CRFای را انتخاب کن که هم محدودیت کیفیت و هم محدودیت حجم را رعایت می‌کند.

---

## ۱. امکانات اصلی

- انکود SVT-AV1 از طریق `libsvtav1` در FFmpeg.
- انکود x265 از طریق FFmpeg.
- پشتیبانی از HandBrakeCLI به عنوان Backend جایگزین.
- Backend/پایپ‌لاین توسعه‌ای برای `SvtAv1EncApp` مستقیم.
- Auto-CRF با متریک‌های:
  - VMAF
  - xPSNR
  - SSIMULACRA2
  - Butteraugli
  - CVVDP
- تست کیفیت قبل از انکود.
- تست کیفیت بعد از انکود.
- ارزیابی نمونه‌ای به جای انکود کامل در هر مرحله‌ی CRF Search.
- تخمین حجم Auto-CRF با منطق سازگار با AB-AV1.
- کش SQLite برای جلوگیری از محاسبات تکراری.
- صف انکود و ادامه‌ی فایل ناقص.
- توقف و لغو پردازش.
- مانیتورینگ CPU/RAM/Power در صورت فراهم بودن ابزارها.
- GOP، Crop، Scale، Deinterlace و فیلترهای سفارشی.
- صوت با Opus/AAC یا Copy.
- حفظ Subtitle در حالت‌های پشتیبانی‌شده.
- حالت Portable یا AppData برای تنظیمات و Cache.

---

# ۲. معماری پروژه

```text
QualiSVT/
│
├─ main.py
│
├─ core/
│  ├─ config.py             مسیر ابزارها، فلگ‌ها و نسخه‌ی Cache
│  ├─ cache_db.py           SQLite Cache + Fingerprint
│  ├─ video_info.py         اطلاعات رسانه با FFprobe
│  ├─ vmaf_utils.py         پارس متریک‌ها و برچسب کیفیت
│  ├─ proc_utils.py         مدیریت درخت پردازه
│  ├─ output_capture.py     ثبت خروجی خطا
│  └─ fs_utils.py           توابع ساده فایل
│
├─ plugins/
│  ├─ base.py               رابط Pluginها
│  ├─ encoder_ffmpeg.py     Backendهای FFmpeg/libsvtav1 و x265
│  ├─ encoder_handbrake.py  Backend مربوط به HandBrakeCLI
│  ├─ encoder_svtav1encapp.py
│  │                         تعریف پایپ‌لاین مستقیم SvtAv1EncApp
│  └─ monitor_ohm.py        مانیتور OpenHardwareMonitor
│
├─ gui/
│  ├─ encoder_app.py        وضعیت اصلی برنامه
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
├─ Bin/                     ابزارهای اجرایی اختیاری
└─ requirements.txt
```

ساختار برنامه بر پایه‌ی **Mixin + Plugin** است؛ یعنی GUI، منطق رسانه، Encoder، Metric، Cache و Monitoring از یکدیگر جدا شده‌اند.

---

# ۳. پیش‌نیازها

ضروری:

- Python 3.x
- FFmpeg
- FFprobe
- FFVship

اختیاری:

- HandBrakeCLI — فقط برای Backend مربوط به HandBrake.
- SvtAv1EncApp — برای Backend مستقیم SVT-AV1.
- `psutil` — برای CPU/RAM Monitoring.
- `tkinterdnd2` — برای Drag & Drop.
- OpenHardwareMonitor — برای خواندن توان CPU در Windowsهای پشتیبانی‌شده.

ابزارهای EXE را می‌توان در `Bin/` کنار برنامه یا داخل `PATH` قرار داد.

اجرای پروژه:

```bash
pip install -r requirements.txt
python main.py
```

---

# ۴. مسیر کلی انکود

در حالت عادی FFmpeg تقریباً این مسیر را طی می‌کند:

```text
Input
  ↓
Demux / Decode
  ↓
Deinterlace / Crop / Scale / Filter
  ↓
SVT-AV1 یا x265
  ↓
Encode/Copy صدا
  ↓
Subtitle Mapping
  ↓
MKV یا MP4
```

ولی در Auto-CRF مسیر متفاوت است:

```text
کل فایل
   ↓
استخراج Sample
   ↓
Encode Sample با CRF مشخص
   ↓
VMAF / xPSNR / FFVship
   ↓
تخمین حجم نهایی
   ↓
قبول یا رد CRF
   ↓
انتخاب CRF بعدی
   ↓
تکرار
```

پس از پایان Search، فقط یک انکود کامل با CRF انتخاب‌شده انجام می‌شود.

---

# ۵. سیستم Sampling

Auto-CRF و Pre/Post Test از Sample استفاده می‌کنند.

تنظیمات اصلی:

- `Sample Duration` — مدت هر نمونه، مثلاً 20 ثانیه.
- `Samples Count` — تعداد ثابت نمونه‌ها؛ مقدار `0` یعنی حالت خودکار.
- `Sample-Every` — فاصله‌ی زمانی نمونه‌ها در حالت خودکار، برحسب دقیقه.
- محل ذخیره Sampleها.
- امکان نگه‌داشتن Sampleها برای بررسی بعدی.

### محاسبه‌ی تعداد Sample خودکار

اگر `Samples Count = 0` باشد:

```text
ceil(Target Duration / Sample Interval)
```

مثلاً:

```text
ویدئو = 87 دقیقه
Interval = 12 دقیقه

ceil(87 / 12) = 8 نمونه
```

Sampleها در نقاط مختلف بازه پخش می‌شوند و همه در ابتدای فایل قرار نمی‌گیرند.

### توزیع نمونه‌ها

برنامه فاصله‌ی خالی بین Sampleها را محاسبه می‌کند تا تقریباً چنین الگویی شکل بگیرد:

```text
[Gap] [Sample] [Gap] [Sample] ... [Gap]
```

بنابراین صحنه‌های متفاوت بیشتر وارد جست‌وجوی CRF می‌شوند.

---

# ۶. استخراج Sample

دو حالت وجود دارد.

### حالت عادی

وقتی Metric به Reference خاص FFVship نیاز ندارد، Sample با کپی Video Stream استخراج می‌شود:

```text
ffmpeg -ss START -i INPUT -frames:v N -c:v copy -an -sn SAMPLE.mkv
```

### Reference برای FFVship

برای متریک‌هایی مانند:

- SSIMULACRA2
- Butteraugli
- CVVDP

در صورت نیاز، Reference با FFV1 به شکل Lossless ساخته می‌شود تا فریم‌ها پایدار و قابل مقایسه باشند.

---

# ۷. متریک‌های کیفیت

## VMAF

VMAF از فیلتر `libvmaf` در FFmpeg استفاده می‌کند و مدل فعلی پروژه:

```text
vmaf_v0.6.1
```

Pooling نیز `Mean` است.

**هرچه بیشتر، بهتر.**

برچسب‌های فعلی:

```text
97+   Visually Lossless
93+   Excellent
85+   Good
75+   Fair
60+   Poor
کمتر از 60   Bad
```

مثال:

```text
VMAF = 97.01
→ Visually Lossless
```

---

## xPSNR

xPSNR از فیلتر `xpsnr` در FFmpeg محاسبه می‌شود.

ترکیب کانال‌ها در کد:

```text
(Y × 4 + U + V) / 6
```

یعنی Luma چهار برابر هر یک از دو کانال Chroma وزن دارد.

**هرچه بیشتر، بهتر.**

آستانه‌ها:

```text
45+   Visually Lossless
42+   Excellent
38+   Good
35+   Fair
30+   Poor
کمتر از 30   Bad
```

---

## SSIMULACRA2

از طریق FFVship اجرا می‌شود.

**هرچه بیشتر، بهتر.**

```text
90+   Visually Lossless
75+   Excellent
60+   Good
50+   Fair
30+   Poor
کمتر از 30   Bad
```

---

## CVVDP

از طریق FFVship اجرا می‌شود.

**هرچه بیشتر، بهتر.**

```text
9.5+  Visually Lossless
8.5+  Excellent
7.5+  Good
6.5+  Fair
5.5+  Poor
کمتر از 5.5  Bad
```

---

## Butteraugli

Butteraugli از FFVship اجرا می‌شود.

برخلاف Metrics بالا، **کمتر بودن بهتر است**.

```text
≤0.5  Visually Lossless
≤1.5  Excellent
≤2.5  Good
≤3.5  Fair
≤4.5  Poor
>4.5   Bad
```

در Auto-CRF نیز همین منطق رعایت می‌شود:

```text
score <= target
```

---

# ۸. Auto-CRF چیست؟

کار Auto-CRF پیدا کردن **بالاترین CRF قابل قبول** است؛ یعنی CRFای که هم:

```text
کیفیت >= هدف
```

و هم:

```text
حجم پیش‌بینی‌شده <= سقف حجم
```

را رعایت کند.

مثلاً:

```text
Metric       = VMAF
Target       = 97
Min CRF      = 20
Max CRF      = 40
Max Size     = 30%
```

هدف این نیست که اولین CRF قابل قبول پیدا شود؛ هدف رسیدن تا بالاترین CRF ممکن در محدوده‌ی جست‌وجو است، با چند بهینه‌سازی برای توقف زودتر.

---

# ۹. شبکه‌ی جست‌وجوی CRF

Step جست‌وجو در کد:

```text
0.5
```

بنابراین CRFها می‌توانند این‌طور باشند:

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

برای محاسبات داخلی:

```text
37.0 → q = 74
37.5 → q = 75
```

---

# ۱۰. CRF اولیه

در شروع، مقدار تقریباً وسط Min و Max تست می‌شود.

مثال:

```text
20 و 40
↓
CRF اولیه ≈ 30
```

به همین دلیل لاگ معمولاً با `CRF 30` شروع می‌شود.

---

# ۱۱. جهت حرکت Search

مثلاً:

```text
CRF 30
VMAF = 98.89
Size = 49.8%
```

کیفیت خوب است ولی حجم بسیار زیاد است؛ پس CRF باید زیاد شود.

سپس:

```text
CRF 40
VMAF = 95.91
Size = 23.7%
```

حجم خوب است ولی کیفیت پایین افتاده؛ پس جواب بین 30 و 40 است.

---

# ۱۲. Interpolation بر اساس Quality

وقتی دو نقطه داریم:

```text
CRF پایین‌تر → Quality بالاتر
CRF بالاتر    → Quality پایین‌تر
```

برنامه با interpolation تخمین می‌زند که Target Quality تقریباً در کجا قرار دارد.

مثلاً:

```text
CRF 36.5 → VMAF 97.18
CRF 40.0 → VMAF 95.91
Target    → VMAF 97.00
```

بنابراین Search به سمت CRF حدود 37 هدایت می‌شود.

---

# ۱۳. مهم‌ترین قسمت: تخمین حجم شبیه AB-AV1

برای هر CRF فقط Sampleها Encode می‌شوند.

دو تخمین ساخته می‌شود.

## روش اول — بر اساس Duration

```text
Duration Estimate =
    Encoded Sample Bytes / Total Sample Duration
    × Full Target Duration
```

مثال:

```text
Encoded Sample = 12 MiB
Total Sample Duration = 60 s
Full Video = 900 s

12 / 60 × 900 = 180 MiB
```

---

## روش دوم — بر اساس درصد حجم Sample

فرمول:

```text
File-Percent Estimate =
    Full Input File Size
    × (Encoded Sample Bytes / Source Sample Bytes)
```

مثال:

```text
Input = 1000 MiB
Source Samples = 100 MiB
Encoded Samples = 4 MiB

1000 × (4 / 100) = 40 MiB
```

---

## انتخاب تخمین نهایی

دو تخمین با هم مقایسه می‌شوند:

```text
Predicted Size = min(Duration Estimate, File-Percent Estimate)
```

مثلاً:

```text
Duration Estimate    = 180 MiB
File-Percent Estimate = 40 MiB

Predicted Size = 40 MiB
```

بعد درصد نسبت به **کل فایل اصلی** محاسبه می‌شود:

```text
Predicted Percent =
    Predicted Size / Full Input Size × 100
```

مثال:

```text
40 / 1000 × 100 = 4%
```

---

# ۱۴. چرا Encoded Size فقط Video Stream است؟

در Auto-CRF، Sampleها بدون Audio Encode می‌شوند:

```text
-an
```

پس Audio نباید وارد تصمیم CRF و VMAF شود.

برای حجم Sample Encode، برنامه ابتدا اندازه‌ی Stream ویدئوی اول را با FFprobe می‌خواند.

اگر `stream=size` مقدار معتبر ندهد، برنامه اندازه‌ی Packetهای Video Stream اول را جمع می‌کند.

این Fallback برای بعضی MKVها ضروری است که FFprobe برای Stream Size مقدار `N/A` می‌دهد.

**توجه:** در شاخه‌ی دوم فرمول File-Percent، اندازه‌ی فایل Sample Source از فایل Sample استخراج‌شده گرفته می‌شود؛ یعنی این بخش صرفاً بر مبنای `video stream bytes` برای هر دو طرف نیست.

---

# ۱۵. قانون قبول/رد Auto-CRF

برای Metricهایی که بیشتر بودنشان بهتر است:

```text
quality_ok = score >= target
```

برای Butteraugli:

```text
quality_ok = score <= target
```

شرط حجم:

```text
size_ok = predicted_percent <= max_percent
```

در نهایت:

```text
size_ok AND quality_ok
```

### مثال

```text
Target VMAF = 97
Max Size    = 30%

CRF 37
VMAF = 97.01
Size = 29.9%

97.01 >= 97  → درست
29.9 <= 30   → درست

نتیجه → Accepted
```

---

# ۱۶. مثال واقعی تست پروژه

برای فایل:

```text
1.mp4
Input Size ≈ 126.9 MB
Target VMAF = 97
Max Size = 30%
Preset = 6
10-bit
```

Search می‌تواند به این نتیجه برسد:

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

بنابراین:

```text
CRF = 37
```

و حجم پیش‌بینی‌شده:

```text
126.9 × 0.299 ≈ 37.9 MB
```

---

# ۱۷. Pre-Encode Quality Test

هدف Pre-Test این است:

> قبل از انکود کامل ببینیم CRF انتخاب‌شده تقریباً چه کیفیتی می‌دهد و چه حجم/زمانی باید انتظار داشت.

مسیر:

```text
Input
 ↓
Sample extraction
 ↓
Encode samples با CRF انتخابی
 ↓
Metric calculation
 ↓
میانگین Quality
 ↓
تخمین حجم
 ↓
تخمین زمان انکود
```

در Pre-Test فعلی، تخمین حجم و زمان بر اساس نرخ Encode Sampleها نسبت به مدت زمان انجام می‌شود.

---

# ۱۸. Post-Encode Test

در Post-Test دیگر تخمین نمی‌زنیم؛ خروجی واقعی را با Source مقایسه می‌کنیم.

برای هر Sample:

```text
Original Sample → Reference
Encoded Sample  → Test
                  ↓
              Metric
```

گزارش می‌تواند شامل:

- Average Score
- Worst Frame
- Mid Frame
- Best Frame

باشد.

اگر حداقل 20 فریم Metric داشته باشیم، 5 درصد ابتدا و 5 درصد انتهای طیف حذف می‌شوند تا Outlierهای شدید روی انتخاب Worst/Mid/Best اثر کمتری داشته باشند.

برای Butteraugli چون کمتر بهتر است، تعریف Worst و Best معکوس می‌شود.

---

# ۱۹. نمایش فریم‌ها

وقتی Sampleها نگه داشته شوند، برنامه می‌تواند Frameهای WebP بسازد:

- Original
- Encoded
- تصویر ترکیبی Side-by-Side

این بخش برای زمانی مهم است که یک عدد Metric دلیل یک Artifact قابل مشاهده را به خوبی توضیح نمی‌دهد.

---

# ۲۰. تنظیمات Video Encoder

Encoderهای اصلی:

```text
SVT-AV1
x265
```

از طریق FFmpeg.

HandBrakeCLI نیز Backend جداگانه دارد.

### Bit Depth

```text
8-bit  → yuv420p
10-bit → yuv420p10le
12-bit → yuv420p12le
```

### SVT Parameters

مثلاً:

```text
tune=0:scd=1:hbd-mds=1:ac-bias=2:enable-variance-boost=1:complex-hvs=1
```

این پارامترها از UI گرفته شده و در مسیر FFmpeg با `-svtav1-params` اعمال می‌شوند.

---

# ۲۱. GOP

QualiSVT می‌تواند GOP/Keyint را خودکار یا دستی مدیریت کند.

مقدار نهایی GOP در Cache Key قرار می‌گیرد، چون تغییر GOP می‌تواند روی نتیجه‌ی کیفیت و حجم تأثیر بگذارد.

---

# ۲۲. Audio

حالت‌های فعلی:

- OPUS
- AAC
- Copy

مثال:

```text
Audio Codec = OPUS
Bitrate     = 128K
```

ولی در Auto-CRF Audio عمداً حذف می‌شود:

```text
-an
```

چون تصمیم CRF مربوط به Video Quality/Video Size است.

---

# ۲۳. Subtitle

Subtitle مستقل از Audio مدیریت می‌شود.

در MKV معمولاً Subtitle Text قابل Copy است.

در MP4، Codecهای متنی پشتیبانی‌شده در صورت امکان به `mov_text` نگاشت می‌شوند.

این جداسازی باعث می‌شود نداشتن Audio باعث حذف اشتباهی Subtitle نشود.

---

# ۲۴. Crop / Scale / Filters

برنامه می‌تواند:

- Original
- 480p
- 720p
- 1080p
- 1440p
- 4K
- Auto Crop
- Deinterlace با `yadif`
- Custom Video Filter
- Custom Audio Filter در Backend FFmpeg

را اعمال کند.

Filterهای نهایی در Cache Key ثبت می‌شوند، چون کوچک‌ترین تغییر در Pixel Pipeline باید نتیجه‌ی Cache قبلی را نامعتبر کند.

---

# ۲۵. Range Mode

صف پردازش می‌تواند روی:

- کل ویدئو
- بازه‌ی زمانی انتخاب‌شده
- Chapter Range در حالت‌های پشتیبانی‌شده

کار کند.

Start Offset و Target Duration نیز در Cache ثبت می‌شوند.

---

# ۲۶. SQLite Cache

برای اینکه محاسبات سنگین Auto-CRF و Quality Test دوباره انجام نشوند، نتایج در SQLite ذخیره می‌شوند.

Cache Key شامل مواردی مثل این‌هاست:

```text
Input Fingerprint
FFmpeg/FFVship/HandBrakeCLI Fingerprint
Encoder
Preset
Tune
CRF
Encoder Parameters
Bit Depth
GOP
Scale/Crop/Deinterlace
Sample Duration
Sample Interval
Sample Count
Seek Offset
Target Duration
Custom Filters
Reference Filters
Cache Schema
Size Estimation Mode
```

### Input Fingerprint

برنامه برای سرعت از این اطلاعات استفاده می‌کند:

```text
File Size
Modification Time (ns)
First 1 MiB
Last 1 MiB
```

پس لازم نیست برای هر Cache Check کل فایل 10 یا 20 گیگابایتی Hash شود.

### چه زمانی Cache را خاموش کنیم؟

وقتی:

- FFmpeg یا FFVship را عوض کرده‌اید.
- Build متفاوتی از Encoder نصب کرده‌اید.
- منطق برنامه را تغییر داده‌اید.
- نتیجه‌ی تست تمیز می‌خواهید.

---

# ۲۷. Queue و Resume

فایل‌ها به صورت صفی پردازش می‌شوند.

در زمان Encode فایل موقت ممکن است این پسوندها را داشته باشد:

```text
file.incomplete
file.001.incomplete
file.002.incomplete
```

اگر Encode قطع شود، برنامه Duration قابل خواندن فایل را بررسی می‌کند و در صورت امکان از همان‌جا ادامه می‌دهد یا فایل کامل را Restore می‌کند.

---

# ۲۸. Pause / Cancel

لایه‌ی Process Control پردازه‌های اصلی و Child Processها را دنبال می‌کند.

قابلیت‌ها:

- Stop
- Pause/Resume طبق پیاده‌سازی پردازه
- Cancel Auto-CRF
- Cancel FFVship
- Cleanup Sampleهای موقت بعد از Cancel

---

# ۲۹. Hardware Monitoring

در صورت فعال بودن Monitoring می‌توان این موارد را داشت:

```text
Average CPU Usage
Peak RAM Usage
Average CPU Package Power
Estimated Energy (Wh)
```

CPU/RAM با `psutil` اندازه‌گیری می‌شوند.

در Windows مناسب، OpenHardwareMonitor از طریق WMI/CIM و PowerShell توان Package CPU را می‌خواند.

مثال:

```text
Average CPU Power = 165 W
Duration          = 1.5 h

Energy ≈ 165 × 1.5 = 247.5 Wh
```

---

# ۳۰. سیستم Plugin

دو رابط اصلی دارد.

## EncoderBackend

مشخص می‌کند یک Encoder چگونه خروجی بسازد.

Backendهای فعلی:

```text
FFmpeg
HandBrakeCLI
SvtAv1EncApp pipeline definition
```

Backend مستقیم SvtAv1EncApp یک پایپ‌لاین سه‌مرحله‌ای تعریف می‌کند:

```text
FFmpeg Decode/Filter
        ↓ Y4M
SvtAv1EncApp
        ↓ IVF
FFmpeg Mux
        ↓
Final Container
```

این بخش فعلاً به مسیر اصلی Single-Process execution برنامه وصل نشده است و بیشتر یک Extension Point آماده است.

## SystemMonitorPlugin

یک منبع Telemetry می‌دهد بدون اینکه GUI مجبور باشد منطق Sensor را بشناسد.

پیاده‌سازی فعلی:

```text
OpenHardwareMonitor
```

در آینده می‌توان Backendهایی مانند LibreHardwareMonitor، NVML یا Linux hwmon اضافه کرد.

---

# ۳۱. مدیریت خطا

برای خطاهای Processهای خارجی برنامه می‌تواند ثبت کند:

- Command کامل
- Return Code
- Tail خروجی برای UI
- خروجی کامل‌تر در Log

خطاهای Fatal زمان Startup نیز در صورت امکان در:

```text
QualiSVT_crash_log.txt
```

ذخیره می‌شوند.

---

# ۳۲. چرا Sample؟

فرض کنید فایل شما ۲ ساعت است و بخواهید این CRFها را روی کل فایل تست کنید:

```text
30
40
36.5
37
```

یعنی چند Encode کامل و سنگین.

اما با مثلاً:

```text
8 × 20 ثانیه = 160 ثانیه Sample
```

برنامه می‌تواند قبل از Encode کامل، تخمینی از:

- Quality
- Output Size
- Encoding Time

به دست بیاورد.

---

# ۳۳. محدودیت Sample-Based Prediction

تخمین Sample تضمین نیست.

دلایل اختلاف ممکن است شامل این موارد باشند:

- Sampleها همه‌ی Sceneهای سخت را پوشش ندهند.
- پیچیدگی Sceneها شدیداً متفاوت باشد.
- تصمیم‌های Encoder به Context کامل ویدئو وابسته باشند.
- Audio و Container Overhead در Sample Encode Auto-CRF حضور ندارند.
- ویدئوهای بسیار کوتاه یا بسیار خاص باعث نوسان تخمین شوند.

پس:

```text
Auto-CRF = جست‌وجوی آماری و هوشمند
نه یک ماشین حساب دقیق حجم Full Encode
```

به همین دلیل Post-Test اهمیت دارد.

---

# ۳۴. تنظیم پیشنهادی برای SVT-AV1

برای Workflow کیفیت‌محور می‌توان از این تنظیم استفاده کرد:

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

در تست نمونه‌ی پروژه:

```text
CRF 37
VMAF 97.01
Estimated Size 29.9%
```

و CRF 37 پذیرفته شد.

---

# ۳۵. نمونه‌ی SVT-AV1 Parameters

مثلاً:

```text
tune=0:scd=1:hbd-mds=1:ac-bias=2:enable-variance-boost=1:complex-hvs=1
```

همراه با:

```text
Preset   = 6
10-bit
```

نتیجه‌ی واقعی همیشه به Build SVT-AV1، منبع، Filterها، محل Sampleها و Build FFmpeg بستگی دارد.

---

# ۳۶. خواندن لاگ Auto-CRF

مثال:

```text
CRF 30
VMAF 98.89
Size 49.8%
Rejected: Size
```

یعنی کیفیت خوب است ولی حجم زیاد است؛ پس CRF باید بالا برود.

بعد:

```text
CRF 40
VMAF 95.91
Size 23.7%
Rejected: VMAF
```

یعنی حجم خوب است ولی کیفیت پایین است؛ پس CRF باید پایین بیاید.

و در نهایت:

```text
CRF 37
VMAF 97.01
Size 29.9%
Target Met!
```

یعنی هر دو شرط رعایت شده‌اند.

---

# ۳۷. فلسفه‌ی پروژه

QualiSVT قرار نیست جای مشاهده‌ی انسانی را بگیرد.

Workflow صحیح:

```text
Encode کمتر
Measure بیشتر
Search هوشمند
Verify خروجی واقعی
```

Metric یک مدرک عددی است، نه جایگزین مشاهده‌ی تصویر.

برای محتوای حساس بهتر است:

```text
Auto-CRF
   ↓
Pre-Test
   ↓
Full Encode
   ↓
Post-Test
   ↓
بررسی Worst/Mid/Best Frames
```

انجام شود.

---

# ۳۸. وضعیت فعلی پروژه

مسیر اصلی و پایدار فعلی، Encode با FFmpeg است.

ساختار پروژه برای توسعه‌ی موارد زیر آماده شده است:

- Encoder Backendهای جدید.
- Metricهای جدید.
- Hardware Monitorهای جدید.
- روش‌های متفاوت Sampling.
- اجرای مستقیم کامل SvtAv1EncApp در آینده.

---

# ۳۹. License و Attribution

در صورت تعیین License رسمی پروژه، متن آن و Attribution ابزارهای ثالث را در این بخش اضافه کنید.

ابزارها/پروژه‌های ثالث مهم:

FFmpeg، SVT-AV1، FFVship، HandBrakeCLI، VMAF و OpenHardwareMonitor.

