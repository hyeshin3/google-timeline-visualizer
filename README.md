# Google Timeline Visualizer

> **Original project by [@mahlernim](https://github.com/mahlernim)**
> This repository is a fork of the original [Google Timeline Visualizer](https://github.com/mahlernim/google-timeline-visualizer). All core visualization logic — distance-based animation, dynamic camera, Web Mercator projection — is the original author's work.
>
> The modifications in this fork were developed with AI assistance (Google Gemini).

Visualize your year in travel using your Google Location History (Timeline) data. This tool generates an animated video of your movements, featuring:

- **Distance-based Animation**: Long trips feel majestic, commutes fly by.
- **Dynamic Camera**: Smart zooming and smoothing to keep the action in frame.
- **Privacy Friendly**: Runs entirely locally on your machine.
- **Web Mercator Projection**: Perfect alignment with map tiles.

## What's Changed in This Fork

This fork adds a **two-step workflow** that gives you more control over how your travel data is displayed:

### 1. Data Preprocessing (`process_travel.py`)
A new preprocessing script that converts the raw Google Timeline export (`location-history.json`) into a cleaner, structured format (`processed_full.json`).

- **City & Country Identification**: Uses a customizable bounding-box database to assign city and country names to each coordinate, instead of relying on runtime reverse geocoding.
- **Airport Detection for Flights**: Automatically identifies departure and arrival airports for flight segments using a built-in airport database (80 km radius matching).
- **Forward Fill**: Fills in `Unknown` city names by carrying forward the last known location.
- **Date Filtering**: Optionally trims data to a specific date range.

### 2. Dynamic Overlay (`visualizer.py` modifications)
The video overlay now displays real-time travel information instead of a static title:

- **Date + Country** (e.g., `01-28 · USA`)
- **City name** (e.g., `San Francisco`)
- **Accumulated distance** (e.g., `9,083 km`)

The overlay text uses Helvetica font and updates dynamically as the animation progresses.

### Other Changes
- **Dual format support**: The visualizer now accepts both the original Google Timeline format and the preprocessed format. Format is auto-detected.
- **Reverse geocoder bypass**: When using preprocessed data, the `reverse_geocoder` library is skipped entirely, resulting in faster rendering.

---

## Result Example
![Travel History Sample](travel_history_sample.gif)

## 1. Download Your Data
You need your "Semantic Location History" JSON file from Google.

**Method 1: Google Maps App (Android & iOS)**
1. Open the Google Maps app.
2. Tap your **Profile Picture** -> **Your Timeline**.
3. Tap the **three dots** (Menu) -> **Settings and privacy**.
4. Select **Export Timeline data**.

**Method 2: Android System Settings**
1. Open **Settings** -> **Location** (or **Location Services**).
2. Tap **Timeline**.
3. Select **Export Timeline data**.

*Note: Save the JSON file to your phone and transfer it to your computer.*

## 2. Setup
1. **Install Python 3.8+**.
2. **Install FFmpeg**: Required for video generation.
    * **Windows**: [Download build](https://gyan.dev/ffmpeg/builds/), extract, and add `bin` folder to your PATH.
    * **Mac**: `brew install ffmpeg`
    * **Linux**: `sudo apt install ffmpeg`

3. **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## 3. Usage

### Option A: Direct Visualization (Original Workflow)
Place your `Timeline.json` in the project folder and run:

```bash
python visualizer.py --input Timeline.json --year 2025 --output my_trip_2025.mp4
```

### Option B: Preprocess + Visualize (Recommended for This Fork)

**Step 1.** Preprocess your data:
```bash
python process_travel.py
```
This reads `location-history.json` and generates `processed_full.json`.

Before running, you can customize `process_travel.py` by editing:
- `AIRPORT_DB` — Add airports for your travel destinations
- `CITY_DB` — Add bounding boxes for cities you visited
- `END_DATE` — Set the end date for filtering (or `None` to include all data)

**Step 2.** Generate the video:
```bash
python visualizer.py --input processed_full.json --year 2025 --output my_trip_2025.mp4
```

### Options
- `--input`, `-i` (Required): Path to your location history JSON file.
- `--year`, `-y`: The year to visualize (default: current year).
- `--output`, `-o`: Output video filename (default: `travel_history.mp4`).
- `--title`, `-t`: Custom title displayed on the video (default: "My Trips").
- `--preview-gif`: Also generate a shorter preview GIF before the full video.
- `--gif-only`: Generate only a GIF (no MP4 video). Implies `--preview-gif`.
- `--preview-output`: Output path for preview GIF.
- `--preview-max-distance-km`: Maximum travel distance (km) to include in the preview GIF. Defaults to 30% of total distance if not set.
- `--flight-speedup`: Speed multiplier for flight segments (>=1). Higher values make flights pass faster in the video (default: 10.0).
- `--bridge-gaps-km`: If >0, interpolate any jump >= this distance (km) even when not tagged as flying (default: 0.0).
- `--bridge-gaps-as-flying`: When bridging gaps, mark interpolated points as 'flying' so `--flight-speedup` applies (default: True).
- `--no-bridge-gaps-as-flying`: When bridging gaps, do not mark interpolated points as 'flying'.

### Example
```bash
python visualizer.py -i processed_full.json -y 2026 -o south_america.mp4 --preview-gif --flight-speedup 10.0
```

## License
See the original repository for license information.
