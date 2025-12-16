# Google Timeline Visualizer
**Author:** [@mahlernim](https://github.com/mahlernim)

Visualize your year in travel using your Google Location History (Timeline) data. This tool generates a beautiful, animated video of your movements, featuring:

-   **Distance-based Animation**: Long trips feel majestic, commutes fly by.
-   **Dynamic Camera**: Smart zooming and smoothing to keep the action in frame without shaking.
-   **Privacy Friendly**: Displays Month/Year only, and runs entirely locally on your machine.
-   **Web Mercator Projection**: Perfect alignment with map tiles.

## Result Example
![Travel History Sample](travel_history_sample.gif)

## 1. Download Your Data
You need your "Semantic Location History" JSON file from Google.

**Method 1: Google Maps App (Android & iOS)**
1.  Open the Google Maps app.
2.  Tap your **Profile Picture** -> **Your Timeline**.
3.  Tap the **three dots** (Menu) in the top-right -> **Settings and privacy**.
4.  Select **Export Timeline data**.

**Method 2: Android System Settings**
1.  Open **Settings** -> **Location** (or **Location Services**).
2.  Tap **Timeline**.
3.  Select **Export Timeline data**.

*Note: Save the JSON file to your phone and transfer it to your computer.*

## 2. Setup
1.  **Install Python 3.8+**.
2.  **Install FFmpeg**: This is required for video generation.
    *   **Windows**: [Download build](https://gyan.dev/ffmpeg/builds/), extract, and add `bin` folder to your PATH.
    *   **Mac**: `brew install ffmpeg`
    *   **Linux**: `sudo apt install ffmpeg`

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## 3. Usage
Place your `Timeline.json` in the project folder.

Run the visualizer:

```bash
python visualizer.py --input Timeline.json --year 2025 --output my_trip_2025.mp4
```

### Options
-   `--input`: Path to your JSON file.
-   `--year`: The year you want to visualize (YYYY).
-   `--output`: Output video filename (default: `travel_history.mp4`).
-   `--title`: Custom title for the video (default: "My Trips").

### Example
```bash
python visualizer.py -i Timeline.json -y 2024 -t "Europe Trip 2024" -o europe.mp4
```
