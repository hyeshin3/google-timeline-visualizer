#!/usr/bin/env python3
"""
Google Timeline Visualizer
Author: @mahlernim
Description:
    Analyzes your Google Location History (Timeline.json) and generates a beautiful
    animated video of your travels for a specific year.
    Features:
    - Distance-based animation speed (majestic long trips, fast commutes)
    - Dynamic Camera (Smart Zoom & Smoothing)
    - Web Mercator Projection for perfect map alignment
    - Privacy-friendly (Month-only timestamps)
"""

import json
import argparse
import math
import sys
import io
import urllib.request
import bisect
from datetime import datetime
from pathlib import Path

# Third-party imports
try:
    import numpy as np
    import matplotlib
    # Set non-interactive backend
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    import matplotlib.font_manager as fm
    import matplotlib.patches as patches
    from PIL import Image
    import dateutil.parser
    import reverse_geocoder as rg
    import pycountry
    
    # Configure fallback fonts for Helvetica
    plt.rcParams['font.sans-serif'] = ['Helvetica', 'Arial', 'sans-serif']
except ImportError as e:
    print(f"Error: Missing dependency {e.name}. Please run: pip install -r requirements.txt")
    sys.exit(1)

# --- CONFIGURATION DEFAULTS ---
DEFAULT_FPS = 30
DEFAULT_DURATION = 90
DEFAULT_TAIL_KM = 500
THEME_COLOR = '#ff0055' # Pink/Red
TILE_URL = "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"

# Camera Physics
SMOOTHING_FACTOR = 0.07 
LOOKAHEAD_KM = 1500 
MIN_ZOOM_SPAN_METERS = 30000 

# Animation Timing
# flying 구간은 동일 거리 대비 더 짧은 시간으로 표현하기 위해 "유효 거리"를 줄여서 처리한다.
# (예: speedup=4.0 이면 비행 구간의 유효 거리는 1/4 → 전체 영상에서 비행 시간이 더 짧아짐)
DEFAULT_FLIGHT_SPEEDUP = 10.0

# Web Mercator Constants
R_EARTH = 6378137.0
MAX_EXTENT = 20037508.342789244

# --- PROJECTION LOGIC ---

def latlon_to_meters(lat, lon):
    x = R_EARTH * math.radians(lon)
    y = R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y

def meters_to_latlon(x, y):
    lon = math.degrees(x / R_EARTH)
    lat = math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2)
    return lat, lon

def haversine_dist(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def generate_geodesic_points(lat1, lon1, lat2, lon2, num_points):
    """
    두 위경도 사이를 구면 보간으로 이으면서 중간 점들을 생성한다.
    반환값에는 시작/끝 점은 포함하지 않고, 중간 점들만 포함한다.
    """
    if num_points <= 0:
        return []

    phi1 = math.radians(lat1)
    lam1 = math.radians(lon1)
    phi2 = math.radians(lat2)
    lam2 = math.radians(lon2)

    v1 = np.array([math.cos(phi1) * math.cos(lam1),
                   math.cos(phi1) * math.sin(lam1),
                   math.sin(phi1)])
    v2 = np.array([math.cos(phi2) * math.cos(lam2),
                   math.cos(phi2) * math.sin(lam2),
                   math.sin(phi2)])

    dot = float(np.clip(np.dot(v1, v2), -1.0, 1.0))
    omega = math.acos(dot)
    if omega == 0:
        return []

    sin_omega = math.sin(omega)
    points = []
    for i in range(1, num_points + 1):
        t = i / (num_points + 1)
        factor1 = math.sin((1 - t) * omega) / sin_omega
        factor2 = math.sin(t * omega) / sin_omega
        v = factor1 * v1 + factor2 * v2
        v = v / np.linalg.norm(v)
        lat = math.degrees(math.asin(v[2]))
        lon = math.degrees(math.atan2(v[1], v[0]))
        points.append((lat, lon))

    return points

def unwrap_longitude_sequence(lons):
    """
    경도 배열을 이전 값과의 차이가 최소가 되도록 ±360을 더/빼서 연속적으로 만든다.
    (날짜변경선(±180°) 통과 시 선이 화면 끝으로 튀는 현상 방지)
    """
    if not lons:
        return []

    out = [float(lons[0])]
    for lon in lons[1:]:
        lon = float(lon)
        prev = out[-1]
        while lon - prev > 180.0:
            lon -= 360.0
        while lon - prev < -180.0:
            lon += 360.0
        out.append(lon)
    return out

# --- MAP TILES (Web Mercator) ---

def meters_to_tile(mx, my, zoom):
    # x는 세계가 반복되므로(가로 래핑) unwrapped tile index를 허용한다.
    # y는 반복되지 않으므로 [0, n-1] 범위로 클램프한다.
    n = int(2 ** zoom)
    world = 2.0 * MAX_EXTENT
    tile_size = world / n

    xtile = int(math.floor((mx + MAX_EXTENT) / tile_size))
    ytile = int(math.floor((MAX_EXTENT - my) / tile_size))
    ytile = max(0, min(n - 1, ytile))
    return xtile, ytile

def tile_to_bounds_meters(xtile, ytile, zoom):
    n = int(2 ** zoom)
    tile_size = (2.0 * MAX_EXTENT) / n
    min_x = -MAX_EXTENT + xtile * tile_size
    max_x = min_x + tile_size
    max_y = MAX_EXTENT - ytile * tile_size
    min_y = max_y - tile_size
    return min_x, max_x, min_y, max_y

TILE_CACHE = {}
def fetch_tile_img(x, y, z):
    # 타일 서버는 x를 [0, 2^z-1] 범위로 래핑해야 한다.
    n = int(2 ** z)
    x_wrapped = x % n
    key = (x_wrapped, y, z)
    if key in TILE_CACHE: return TILE_CACHE[key]
    url = TILE_URL.format(z=z, x=x_wrapped, y=y)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            img = Image.open(io.BytesIO(response.read())).convert('RGB')
            TILE_CACHE[key] = img
            return img
    except Exception:
        # Return fallback tile (gray)
        return Image.new('RGB', (256, 256), (240, 240, 240))

def get_map_image(x_center, y_center, span, width_px=800):
    # Calculate target zoom
    # Resolution (m/px) needed = span / width_px
    # Base resolution (z=0) = (2 * MAX_EXTENT) / 256
    # Res at z = Base / 2^z
    # 2^z = (Base / Res_needed) = (2*MAX / 256) / (span / width)
    # 2^z = (2 * MAX * width) / (256 * span)
    
    target_val = (2 * MAX_EXTENT * width_px) / (256 * max(span, 1.0))
    zoom = int(math.log2(target_val)) if target_val > 0 else 2
    zoom = max(2, min(15, zoom)) # Limit zoom range
    
    min_x = x_center - span/2
    max_x = x_center + span/2
    min_y = y_center - span/2
    max_y = y_center + span/2
    
    xt_min, yt_min = meters_to_tile(min_x, max_y, zoom)
    xt_max, yt_max = meters_to_tile(max_x, min_y, zoom)
    
    if yt_min > yt_max: yt_min, yt_max = yt_max, yt_min
    
    x_tiles = xt_max - xt_min + 1
    y_tiles = yt_max - yt_min + 1
    
    # Safety clamp: if view is too huge, reduce zoom
    while x_tiles * y_tiles > 25 and zoom > 2:
        zoom -= 1
        xt_min, yt_min = meters_to_tile(min_x, max_y, zoom)
        xt_max, yt_max = meters_to_tile(max_x, min_y, zoom)
        if yt_min > yt_max: yt_min, yt_max = yt_max, yt_min
        x_tiles = xt_max - xt_min + 1
        y_tiles = yt_max - yt_min + 1

    tile_w, tile_h = 256, 256
    stitched = Image.new('RGB', (x_tiles * tile_w, y_tiles * tile_h))
    
    # Calculate exact bounds of the stitched image
    tl_min_x, tl_max_x, tl_min_y, tl_max_y = tile_to_bounds_meters(xt_min, yt_min, zoom)
    br_min_x, br_max_x, br_min_y, br_max_y = tile_to_bounds_meters(xt_max, yt_max, zoom)
    
    final_min_x = tl_min_x
    final_max_x = br_max_x # technically bounds of xt_max tile
    final_max_y = tl_max_y
    final_min_y = br_min_y
    
    for x in range(x_tiles):
        for y in range(y_tiles):
            img = fetch_tile_img(xt_min + x, yt_min + y, zoom)
            stitched.paste(img, (x * tile_w, y * tile_h))
            
    return stitched, (final_min_x, final_max_x, final_min_y, final_max_y)

# --- DATA PROCESSING ---

def parse_timeline(input_path, year, flight_speedup, bridge_gaps_km=0.0, bridge_gaps_as_flying=True):
    print(f"Loading {input_path}...")
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading JSON: {e}")
        sys.exit(1)

    is_flattened = isinstance(data, list) and len(data) > 0 and 'start_time' in data[0]

    if is_flattened:
        print(f"Parsing {len(data)} flattened segments for year {year}...")
        segments = [] # not used
    elif isinstance(data, dict):
        segments = data.get('semanticSegments', [])
        print(f"Parsing {len(segments)} segments for year {year}...")
    elif isinstance(data, list):
        segments = data
        print(f"Parsing {len(segments)} segments for year {year}...")
    else:
        print("Unsupported JSON format")
        sys.exit(1)

    points = []

    if is_flattened:
        for seg in data:
            try:
                start_dt = dateutil.parser.parse(seg['start_time'])
            except Exception:
                continue
            if start_dt.year != year:
                continue
            
            mode = seg.get('type')
            city = seg.get('city', 'Unknown')
            country = seg.get('country', 'Unknown')
            
            if 'start_lat' in seg and 'end_lat' in seg:
                end_dt = dateutil.parser.parse(seg.get('end_time', seg['start_time']))
                end_city = seg.get('city', city)
                end_country = seg.get('country', country)
                if 'dep_city' in seg:
                    city = seg['dep_city']
                    country = seg.get('dep_country', country)

                points.append({
                    'dt': start_dt, 'lat': float(seg['start_lat']), 'lon': float(seg['start_lon']),
                    'mode': mode, 'city': city, 'country': country
                })
                points.append({
                    'dt': end_dt, 'lat': float(seg['end_lat']), 'lon': float(seg['end_lon']),
                    'mode': mode, 'city': end_city, 'country': end_country
                })
            elif 'lat' in seg:
                points.append({
                    'dt': start_dt, 'lat': float(seg['lat']), 'lon': float(seg['lon']),
                    'mode': mode, 'city': city, 'country': country
                })
    else:
        for seg in segments:
            start_str = seg.get('startTime')
            if not start_str:
                continue
            try:
                dt = dateutil.parser.parse(start_str)
            except Exception:
                continue
    
            if dt.year != year:
                continue
    
            visit = seg.get('visit')
            if visit:
                top = visit.get('topCandidate', {})
                loc = top.get('placeLocation')
                mode = top.get('type')
                if isinstance(loc, str) and loc.startswith("geo:"):
                    try:
                        coord = loc[4:]
                        lat_str, lon_str = coord.split(',')
                        lat = float(lat_str)
                        lon = float(lon_str)
                        points.append({'dt': dt, 'lat': lat, 'lon': lon, 'mode': mode})
                    except Exception:
                        pass
    
            activity = seg.get('activity')
            if activity:
                act_top = activity.get('topCandidate', {})
                act_mode = None
                if isinstance(act_top, dict):
                    act_mode = act_top.get('type')
    
                for key in ('start', 'end'):
                    loc = activity.get(key)
                    if isinstance(loc, str) and loc.startswith("geo:"):
                        try:
                            coord = loc[4:]
                            lat_str, lon_str = coord.split(',')
                            lat = float(lat_str)
                            lon = float(lon_str)
                            points.append({'dt': dt, 'lat': lat, 'lon': lon, 'mode': act_mode})
                        except Exception:
                            pass

    if not points:
        print(f"No data points found for year {year}.")
        sys.exit(1)

    points.sort(key=lambda x: x['dt'])
    print(f"Found {len(points)} valid points before flight smoothing.")

    # 비행 구간(flying)에 대해서만 구면 보간을 사용해 중간 점을 추가해 비행 경로를 더 자연스럽게 만든다.
    # 또한 큰 거리 점프(비행/누락 데이터 등) 구간은 옵션으로 자동 보간해 "끊김"을 줄인다.
    expanded_points = []
    if points:
        expanded_points.append(points[0])
        for i in range(1, len(points)):
            prev = expanded_points[-1]
            curr = points[i]
            prev_mode = prev.get('mode')
            curr_mode = curr.get('mode')
            is_flight = (prev_mode == 'flying') or (curr_mode == 'flying')
            d = haversine_dist(prev['lat'], prev['lon'], curr['lat'], curr['lon'])
            should_bridge_gap = bool(bridge_gaps_km) and (d >= float(bridge_gaps_km))
            should_interpolate = is_flight or should_bridge_gap

            if should_interpolate:
                # 긴 구간일수록 조금 더 많은 점을 추가한다.
                base_segments = 32
                extra_segments = max(base_segments, int(d / 200))  # 대략 200km당 한 세그먼트
                mid_points = generate_geodesic_points(prev['lat'], prev['lon'],
                                                      curr['lat'], curr['lon'],
                                                      extra_segments)
                total = len(mid_points)
                for idx, (mlat, mlon) in enumerate(mid_points, start=1):
                    frac = idx / (total + 1)
                    dt_mid = prev['dt'] + (curr['dt'] - prev['dt']) * frac
                    mid_mode = 'flying' if (is_flight or bridge_gaps_as_flying) else curr_mode
                    expanded_points.append({
                        'dt': dt_mid,
                        'lat': mlat,
                        'lon': mlon,
                        'mode': mid_mode,
                        'city': curr.get('city'),
                        'country': curr.get('country')
                    })
                expanded_points.append(curr)
            else:
                expanded_points.append(curr)

    points = expanded_points
    print(f"Using {len(points)} points after flight smoothing.")

    # 날짜변경선(±180°) 근처에서 경도가 래핑되며 선이 화면 가장자리로 튀는 문제를 방지한다.
    # 비행 구간에서 특히 잘 드러나지만, 경도 연속성은 전체 포인트 시퀀스에 대해 보장해야 한다.
    unwrapped = []
    if points:
        lon_seq = [p.get('lon') for p in points]
        lon_seq = unwrap_longitude_sequence(lon_seq)
        for p, lon_u in zip(points, lon_seq):
            pp = dict(p)
            pp['lon'] = lon_u
            unwrapped.append(pp)
    points = unwrapped

    timestamps = []
    lats = []
    lons = []
    modes = []
    xs = []
    ys = []
    native_locations = []
    has_native = False

    for p in points:
        timestamps.append(p['dt'])
        lats.append(p['lat'])
        lons.append(p['lon'])
        modes.append(p.get('mode'))
        
        if 'city' in p and 'country' in p and p['city'] and p['country']:
            has_native = True
            native_locations.append({'city': p['city'], 'country': p['country']})
        else:
            native_locations.append({'city': 'Unknown', 'country': 'Unknown'})
            
        x, y = latlon_to_meters(p['lat'], p['lon'])
        xs.append(x)
        ys.append(y)

    if not has_native:
        native_locations = None

    # 누적 거리(실거리)와 누적 유효거리(애니메이션 시간축)를 분리한다.
    cum_dist_real = [0.0]
    cum_dist_effective = [0.0]
    total_real = 0.0
    total_effective = 0.0

    if not flight_speedup or flight_speedup < 1.0:
        flight_speedup = 1.0
    flight_weight = 1.0 / float(flight_speedup)

    for i in range(1, len(lats)):
        d = haversine_dist(lats[i - 1], lons[i - 1], lats[i], lons[i])
        is_flight = (modes[i] == 'flying') or (modes[i - 1] == 'flying') or (str(modes[i]).upper() == 'FLYING')
        
        total_real += d
        cum_dist_real.append(total_real)

        total_effective += d * (flight_weight if is_flight else 1.0)
        cum_dist_effective.append(total_effective)

    return timestamps, xs, ys, cum_dist_effective, cum_dist_real, lats, lons, modes, native_locations

def main():
    parser = argparse.ArgumentParser(description="Google Timeline Visualizer")
    parser.add_argument('--input', '-i', required=True, help="Path to Timeline.json")
    parser.add_argument('--year', '-y', type=int, default=datetime.now().year, help="Year to visualize")
    parser.add_argument('--output', '-o', default='travel_history.mp4', help="Output video path")
    parser.add_argument('--title', '-t', default="My Trips", help="Title displayed on video")
    parser.add_argument('--preview-gif', action='store_true', help="Also generate a shorter preview GIF before the full video")
    parser.add_argument('--gif-only', action='store_true', help="Generate only a GIF (no MP4 video). Implies --preview-gif.")
    parser.add_argument('--preview-output', default=None, help="Output path for preview GIF (default: same as --output but with .gif extension)")
    parser.add_argument('--preview-max-distance-km', type=float, default=None, help="Maximum travel distance (km) to include in the preview GIF. If not set, defaults to 30% of total distance.")
    parser.add_argument('--flight-speedup', type=float, default=DEFAULT_FLIGHT_SPEEDUP, help="Speed multiplier for segments tagged as flying (>=1). Higher is faster (shorter).")
    parser.add_argument('--bridge-gaps-km', type=float, default=0.0, help="If >0, interpolate (geodesic) any jump >= this distance (km) even when not tagged as flying.")
    parser.add_argument('--bridge-gaps-as-flying', action='store_true', default=True, help="When bridging gaps, mark interpolated points as 'flying' so --flight-speedup applies.")
    parser.add_argument('--no-bridge-gaps-as-flying', action='store_false', dest='bridge_gaps_as_flying', help="When bridging gaps, do not mark interpolated points as 'flying'.")
    
    args = parser.parse_args()

    if args.flight_speedup is None or args.flight_speedup < 1.0:
        print("Error: --flight-speedup must be >= 1")
        sys.exit(1)
    if args.bridge_gaps_km is None or args.bridge_gaps_km < 0.0:
        print("Error: --bridge-gaps-km must be >= 0")
        sys.exit(1)
    
    # Load
    timestamps, xs, ys, cum_dist_effective, cum_dist_real, lats, lons, modes, native_locations = parse_timeline(
        args.input,
        args.year,
        args.flight_speedup,
        bridge_gaps_km=args.bridge_gaps_km,
        bridge_gaps_as_flying=args.bridge_gaps_as_flying,
    )
    
    total_km_real = cum_dist_real[-1]
    total_km_effective = cum_dist_effective[-1]
    print(f"Total distance: {total_km_real:.1f} km (effective: {total_km_effective:.1f} km, flight speedup: {args.flight_speedup}x)")
    
    # Prepare Frame Indices (Distance Based)
    total_frames = DEFAULT_FPS * DEFAULT_DURATION
    km_per_frame = total_km_effective / total_frames
    
    print(f"Target: {DEFAULT_DURATION}s @ {DEFAULT_FPS}fps. {km_per_frame:.3f} km/frame")
    
    frames_dist = [i * km_per_frame for i in range(total_frames)]
    frame_indices = []
    for d in frames_dist:
        idx = bisect.bisect_left(cum_dist_effective, d)
        idx = min(idx, len(cum_dist_effective)-1)
        frame_indices.append(idx)

    # Preview frame count (for optional GIF)
    preview_frame_count = len(frame_indices)
    if getattr(args, 'preview_gif', False) or getattr(args, 'gif_only', False):
        if args.preview_max_distance_km is not None and args.preview_max_distance_km > 0:
            max_d = min(args.preview_max_distance_km, total_km_real)
        else:
            max_d = total_km_real * 0.3
        # 프리뷰는 지정 거리 이내의 프레임까지만 포함
        preview_frame_count = max(1, sum(1 for idx in frame_indices if cum_dist_real[idx] <= max_d))
        
    # Camera Calculation
    print("Calculating camera path...")
    cam_centers = []
    cam_spans = []
    
    # Reverse Geocode all points (ensure longitudes are wrapped back to [-180, 180] for the geocoder)
    if native_locations:
        print("Using native locations from JSON. Bypassing reverse geocoder...")
        raw_locations = native_locations
    else:
        print("Reverse geocoding locations...")
        wrapped_lons = [((lon + 180) % 360) - 180 for lon in lons]
        coords = tuple(zip(lats, wrapped_lons))
        try:
            location_data = rg.search(coords)
        except Exception as e:
            print(f"Warning: Reverse geocoding failed: {e}")
            location_data = [{'name': 'Unknown', 'cc': 'Unknown'}] * len(coords)

        # Cache country names
        country_cache = {}
        raw_locations = []
        for loc in location_data:
            city = loc.get('name', 'Unknown')
            admin1 = loc.get('admin1', '')
            admin2 = loc.get('admin2', '')
            cc = loc.get('cc', 'Unknown')
            
            if cc == 'KR':
                major_cities = ['Seoul', 'Busan', 'Incheon', 'Daegu', 'Daejeon', 'Gwangju', 'Ulsan', 'Jeju', 'Sejong']
                if admin1 in major_cities:
                    city = admin1
                elif admin2:
                    city = admin2
            elif cc == 'JP':
                major_jp = ['Tokyo', 'Kyoto', 'Osaka', 'Hokkaido']
                if admin1 in major_jp and not city:
                    city = admin1
                    
            # Clean up some common trailing administrative terms for cleaner UI
            if city != 'Unknown':
                city = city.replace(' Town', '').replace(' Village', '').replace(' City', '')
                if cc == 'KR':
                    city = city.replace('-si', '').replace('-gun', '').replace('-gu', '')
            
            if cc not in country_cache:
                country_cache[cc] = get_country_name(cc)
            country = country_cache[cc]
            raw_locations.append({'city': city, 'country': country})
        
    # Forward-fill flight destinations
    frame_locations = list(raw_locations)
    for i in range(len(modes)):
        current_mode = str(modes[i]).upper() if modes[i] else ''
        if 'FLYING' in current_mode or 'FLIGHT' in current_mode:
            dest_idx = i
            for j in range(i + 1, len(modes)):
                dest_idx = j
                next_mode = str(modes[j]).upper() if modes[j] else ''
                if not ('FLYING' in next_mode or 'FLIGHT' in next_mode):
                    break
            frame_locations[i] = raw_locations[dest_idx]
    
    curr_x, curr_y = xs[0], ys[0]
    curr_span = 10000.0 # Start with 10km view
    
    for i, frame_d in enumerate(frames_dist):
        idx = frame_indices[i]
        
        # Lookahead
        target_d = frame_d + LOOKAHEAD_KM
        look_idx = bisect.bisect_left(cum_dist_effective, target_d)
        look_idx = min(look_idx, len(cum_dist_effective)-1)
        
        # Get bounds of window
        w_xs = xs[idx : look_idx+1] or [xs[idx]]
        w_ys = ys[idx : look_idx+1] or [ys[idx]]
        
        min_x, max_x = min(w_xs), max(w_xs)
        min_y, max_y = min(w_ys), max(w_ys)
        
        span_x = max_x - min_x
        span_y = max_y - min_y
        
        target_span = max(span_x, span_y, MIN_ZOOM_SPAN_METERS) * 3.0
        
        # Center Target (Current Position implies following the dot)
        t_x, t_y = xs[idx], ys[idx]
        
        # Update
        curr_x += (t_x - curr_x) * SMOOTHING_FACTOR
        curr_y += (t_y - curr_y) * SMOOTHING_FACTOR
        curr_span += (target_span - curr_span) * (SMOOTHING_FACTOR * 0.5)
        
        cam_centers.append((curr_x, curr_y))
        cam_spans.append(curr_span)
        
    # Visualization
    print("Setting up animation...")
    # 인스타그램 릴스(9:16 비율)에 맞춘 세로형 캔버스
    fig, ax = plt.subplots(figsize=(9, 16))
    # Remove whitespace
    fig.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=None, hspace=None)
    ax.axis('off')
    
    # Init Layers
    # Initial Map
    init_cx, init_cy = cam_centers[0]
    init_span = cam_spans[0]
    init_img, init_ext = get_map_image(init_cx, init_cy, init_span)
    
    map_layer = ax.imshow(init_img, extent=init_ext, aspect='equal')
    
    path_line, = ax.plot([], [], color=THEME_COLOR, alpha=0.5, linewidth=2)
    tail_line, = ax.plot([], [], color=THEME_COLOR, linewidth=4, alpha=1.0)
    head_point, = ax.plot([], [], color='black', marker='o', markersize=8, markeredgecolor=THEME_COLOR)
    
    # UI Elements
    # White fading header background (top 15% of the screen)
    header_rect = patches.Rectangle((0, 0.85), 1, 0.15, transform=ax.transAxes, facecolor='white', edgecolor='none', zorder=5, alpha=0.9)
    ax.add_patch(header_rect)
    
    # Date and Country (e.g., 01-28 • USA) - gray, normal weight
    date_country_text = ax.text(0.5, 0.965, '', transform=ax.transAxes, 
                         color='#666666', fontsize=18, fontweight='normal', ha='center', va='top', zorder=6)
                         
    # City (e.g., San Francisco) - black, bold weight
    city_text = ax.text(0.5, 0.925, '', transform=ax.transAxes, 
                        color='#111111', fontsize=28, fontweight='bold', ha='center', va='top', zorder=6)

    # Accumulated Distance (e.g., 1,234 km) - dark gray, normal weight
    dist_text = ax.text(0.5, 0.875, '', transform=ax.transAxes, 
                        color='#444444', fontsize=16, fontweight='normal', ha='center', va='top', zorder=6)

    # Try different font fallbacks for emoji. Segoe UI Emoji is default on Windows.
    emoji_font = 'Segoe UI Emoji' if 'Segoe UI Emoji' in [f.name for f in fm.fontManager.ttflist] else 'sans-serif'
    emoji_text = ax.text(0, 0, '', color='black', fontsize=24, ha='center', va='bottom', fontfamily=emoji_font)

    def update(i):
        frame_idx = frame_indices[i]
        cx, cy = cam_centers[i]
        span = cam_spans[i]
        
        ax.set_xlim(cx - span/2, cx + span/2)
        ax.set_ylim(cy - span/2, cy + span/2)
        
        # dynamic map update (every 5 frames)
        if i % 5 == 0:
            img, ext = get_map_image(cx, cy, span)
            map_layer.set_data(img)
            map_layer.set_extent(ext)
            
        # Path
        _xs = xs[:frame_idx+1]
        _ys = ys[:frame_idx+1]
        path_line.set_data(_xs, _ys)
        
        # Tail
        curr_km = cum_dist_real[frame_idx]
        start_km = max(0, curr_km - DEFAULT_TAIL_KM)
        start_idx = bisect.bisect_left(cum_dist_real, start_km)
        
        txs = xs[start_idx : frame_idx+1]
        tys = ys[start_idx : frame_idx+1]
        tail_line.set_data(txs, tys)
        
        if _xs:
            head_point.set_data([_xs[-1]], [_ys[-1]])
            
            # Update emoji based on current mode
            current_mode = modes[frame_idx]
            emoji = ''
            if current_mode:
                mode_str = str(current_mode).upper()
                if 'FLYING' in mode_str or 'FLIGHT' in mode_str:
                    emoji = '✈️'
                elif 'VEHICLE' in mode_str or 'DRIVING' in mode_str or 'CAR' in mode_str:
                    emoji = '🚗'
                elif 'BUS' in mode_str:
                    emoji = '🚌'
                elif 'TRAIN' in mode_str:
                    emoji = '🚆'
                elif 'CYCLING' in mode_str or 'BICYCLE' in mode_str:
                    emoji = '🚲'
                elif 'WALKING' in mode_str or 'ON_FOOT' in mode_str:
                    emoji = '🚶'
                    
            emoji_text.set_text(emoji)
            emoji_text.set_position((_xs[-1], _ys[-1] + span/30))
            
        if timestamps:
            dt = timestamps[frame_idx]
            date_str = dt.strftime('%m-%d')
            loc = frame_locations[frame_idx]
            country_str = loc['country']
            city_str = loc['city']
            
            date_country_text.set_text(f"{date_str} • {country_str}")
            city_text.set_text(f"{city_str}")
            
            dist_km = cum_dist_real[frame_idx]
            dist_text.set_text(f"{int(dist_km):,} km")
            
        return map_layer, path_line, tail_line, head_point, date_country_text, city_text, dist_text, emoji_text,

    # Optional preview GIF (shorter segment)
    if getattr(args, 'preview_gif', False) or getattr(args, 'gif_only', False):
        print(f"Generating {preview_frame_count} frames for preview GIF...")
        ani_preview = animation.FuncAnimation(fig, update, frames=preview_frame_count, blit=False)

        preview_output = args.preview_output
        if not preview_output:
            preview_output = str(Path(args.output).with_suffix('.gif'))

        print(f"Saving preview GIF to {preview_output}...")
        # 9x16 inch * 120 dpi = 1080x1920 해상도
        ani_preview.save(preview_output, writer='pillow', fps=DEFAULT_FPS, dpi=120)

    if not getattr(args, 'gif_only', False):
        print(f"Generating {len(frame_indices)} frames for full video...")
        ani = animation.FuncAnimation(fig, update, frames=len(frame_indices), blit=False)
        
        print(f"Saving full video to {args.output}...")
        # 9x16 inch * 120 dpi = 1080x1920 해상도
        ani.save(args.output, writer='ffmpeg', fps=DEFAULT_FPS, dpi=120)
    print("Done!")

if __name__ == "__main__":
    main()
