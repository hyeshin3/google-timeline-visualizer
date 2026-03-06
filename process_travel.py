"""
process_travel.py
─────────────────────────────────────────────────────────────────
Google Timeline (location-history.json) → 여행 기록 데이터 변환

처리 순서:
  1. 레코드 파싱    : visit / activity 분리
  2. 도시 판별      : flying → 공항 DB,  일반 이동 → bounding box DB
  3. 날짜 필터      : END_DATE 이후 레코드 제거
  4. Forward fill  : city == "Unknown"인 레코드를 직전 Known 값으로 채움
  5. 결과 저장      : 전체 JSON

사용법:
  python process_travel.py
  → processed_full.json 생성

커스터마이징:
  - 공항 추가   : AIRPORT_DB 튜플 추가
  - 도시 추가   : CITY_DB 튜플 추가 (범위가 좁을수록 우선 매칭)
  - 날짜 변경   : END_DATE 값 수정 ("YYYY-MM-DD", None이면 필터 없음)
"""

import json
import math
from datetime import datetime
from collections import defaultdict


# ══════════════════════════════════════════════════════════════
# 설정값  ── 여기만 수정하면 됩니다
# ══════════════════════════════════════════════════════════════
INPUT_PATH = "location-history.json"  # 원본 타임라인 파일
OUT_JSON   = "processed_full.json"    # 레코드별 전체 결과
END_DATE   = "2026-02-27"             # 이 날짜까지만 포함 (None = 필터 없음)


# ══════════════════════════════════════════════════════════════
# 1. 공항 데이터베이스
#    비행(flying) 레코드의 도착 좌표 → 공항 IATA → 서비스 도시
#    반경 80km 이내 최근접 공항을 자동 선택
# ══════════════════════════════════════════════════════════════
AIRPORT_DB = [
    # (위도,    경도,     IATA,  공항명,                        서비스 도시,      국가)
    ( 37.4602,  126.4407, "ICN", "Incheon Intl",               "Seoul",          "South Korea"),
    ( 37.5665,  126.9780, "GMP", "Gimpo Intl",                 "Seoul",          "South Korea"),
    ( 37.6189, -122.3750, "SFO", "San Francisco Intl",         "San Francisco",  "United States"),
    ( 33.6367,  -84.4281, "ATL", "Hartsfield-Jackson Atlanta", "Atlanta",        "United States"),
    ( 19.4361,  -99.0719, "MEX", "Benito Juarez Intl",         "Mexico City",    "Mexico"),
    (-12.0219,  -77.1143, "LIM", "Jorge Chavez Intl",          "Lima",           "Peru"),
    (-13.5355,  -71.9388, "CUZ", "Alejandro Velasco Astete",   "Cusco",          "Peru"),
    (-22.8099,  -43.2505, "GIG", "Galeao Intl",                "Rio de Janeiro", "Brazil"),
    (-23.4356,  -46.4731, "GRU", "Sao Paulo Guarulhos",        "Sao Paulo",      "Brazil"),
    (-25.5285,  -49.1758, "CWB", "Afonso Pena Intl",           "Curitiba",       "Brazil"),
    (-25.7166,  -54.4892, "IGU", "Cataratas Intl",             "Foz do Iguacu",  "Brazil"),
    (-22.4953,  -68.9037, "CJC", "El Loa Airport",             "Calama",         "Chile"),
    (-33.3930,  -70.7858, "SCL", "Arturo Merino Benitez",      "Santiago",       "Chile"),
    (-51.6089,  -69.3076, "PUQ", "Carlos Ibanez del Campo",    "Punta Arenas",   "Chile"),
    (-51.6682,  -72.5295, "MHC", "Mocopulli Airport",          "Puerto Natales", "Chile"),
    (-34.8222,  -58.5358, "EZE", "Ministro Pistarini Intl",    "Buenos Aires",   "Argentina"),
    (-34.5592,  -58.4156, "AEP", "Jorge Newbery Airfield",     "Buenos Aires",   "Argentina"),
]


# ══════════════════════════════════════════════════════════════
# 2. 도시 판별 DB (bounding box)
#    좌표가 여러 범위에 걸칠 경우 면적이 좁은 쪽을 우선 매칭
#    범위를 추가할수록 Unknown이 줄어듦
# ══════════════════════════════════════════════════════════════
CITY_DB = [
    # (lat_min, lat_max, lon_min, lon_max, 도시명,                국가)

    # ── 한국
    ( 37.40,  37.72,  126.73, 127.18, "Seoul",                 "South Korea"),

    # ── 미국
    ( 37.20,  37.88, -122.55,-121.90, "San Francisco",         "United States"),
    ( 33.50,  33.90,  -84.55, -84.20, "Atlanta",               "United States"),

    # ── 멕시코
    ( 19.20,  19.60,  -99.35, -98.95, "Mexico City",           "Mexico"),
    ( 17.00,  17.80,  -97.95, -97.40, "Oaxaca",                "Mexico"),

    # ── 페루
    (-12.25, -11.80,  -77.20, -76.85, "Lima",                  "Peru"),
    (-14.30, -13.95,  -76.00, -75.40, "Paracas",               "Peru"),
    (-13.65, -13.40,  -72.70, -72.00, "Aguas Calientes",       "Peru"),
    (-13.35, -13.10,  -72.55, -72.10, "Ollantaytambo",         "Peru"),
    (-13.75, -13.40,  -72.15, -71.75, "Cusco",                 "Peru"),
    (-16.00, -15.60,  -70.20, -69.80, "Puno",                  "Peru"),

    # ── 볼리비아
    (-16.60, -16.30,  -68.25, -67.90, "La Paz",                "Bolivia"),
    (-20.60, -19.80,  -67.30, -66.60, "Uyuni",                 "Bolivia"),
    (-21.60, -20.90,  -67.80, -66.80, "Potosi Region",         "Bolivia"),

    # ── 칠레
    (-22.60, -22.30,  -69.00, -68.70, "Calama",                "Chile"),
    (-23.20, -22.80,  -68.30, -67.90, "San Pedro de Atacama",  "Chile"),
    (-23.40, -22.50,  -68.50, -67.50, "Atacama Desert",        "Chile"),
    (-27.50, -26.80,  -71.00, -70.30, "Atacama Region",        "Chile"),
    (-33.65, -33.20,  -70.90, -70.40, "Santiago",              "Chile"),
    (-51.80, -51.40,  -72.70, -72.20, "Puerto Natales",        "Chile"),
    (-51.30, -50.80,  -73.30, -72.80, "Torres del Paine",      "Chile"),
    (-50.60, -50.10,  -73.30, -72.80, "Torres del Paine",      "Chile"),
    (-50.60, -49.80,  -73.10, -72.10, "Torres del Paine",      "Chile"),

    # ── 아르헨티나
    (-34.95, -34.60,  -58.70, -58.30, "Buenos Aires",          "Argentina"),
    (-25.85, -25.55,  -54.65, -54.30, "Puerto Iguazu",         "Argentina"),
    (-49.60, -49.10,  -73.10, -72.60, "El Chalten",            "Argentina"),

    # ── 브라질
    (-25.65, -25.45,  -54.65, -54.35, "Foz do Iguacu",         "Brazil"),
    (-23.10, -22.60,  -43.45, -43.05, "Rio de Janeiro",        "Brazil"),
    (-23.00, -22.50,  -44.50, -44.00, "Paraty",                "Brazil"),
    (-23.60, -22.50,  -47.00, -45.80, "Sao Paulo",             "Brazil"),
]


# ══════════════════════════════════════════════════════════════
# 3. 유틸리티 함수
# ══════════════════════════════════════════════════════════════
def haversine(lat1, lon1, lat2, lon2):
    """두 좌표 간 거리(km) 계산"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def parse_geo(geo_str):
    """'geo:37.5,127.0' → (37.5, 127.0)"""
    p = geo_str.replace("geo:", "").split(",")
    return float(p[0]), float(p[1])


def parse_time(t):
    return datetime.fromisoformat(t)


def find_nearest_airport(lat, lon, max_km=80):
    """좌표에서 반경 max_km 이내 가장 가까운 공항 반환, 없으면 None"""
    best, best_d = None, float("inf")
    for ap in AIRPORT_DB:
        d = haversine(lat, lon, ap[0], ap[1])
        if d < best_d:
            best_d, best = d, ap
    return (best + (best_d,)) if best and best_d <= max_km else None


def lookup_city(lat, lon):
    """좌표를 CITY_DB에서 찾아 (도시명, 국가명) 반환. 없으면 ('Unknown', 'Unknown')"""
    sorted_db = sorted(CITY_DB, key=lambda x: (x[1] - x[0]) * (x[3] - x[2]))
    for lat_min, lat_max, lon_min, lon_max, city, country in sorted_db:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return city, country
    return "Unknown", "Unknown"


# ══════════════════════════════════════════════════════════════
# 4. 타임라인 파싱 및 도시 판별
# ══════════════════════════════════════════════════════════════
def process_timeline(input_path):
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    results, total_km = [], 0.0
    print(f"총 {len(data)}개 레코드 처리 시작...\n")

    for record in data:
        start_time = parse_time(record["startTime"])
        date_str   = start_time.strftime("%Y-%m-%d")

        # ── VISIT (체류)
        if "visit" in record:
            geo = record["visit"].get("topCandidate", {}).get("placeLocation", "")
            if not geo:
                continue
            lat, lon = parse_geo(geo)
            city, country = lookup_city(lat, lon)
            results.append({
                "type":          "visit",
                "date":          date_str,
                "start_time":    record["startTime"],
                "end_time":      record["endTime"],
                "lat":           lat,
                "lon":           lon,
                "city":          city,
                "country":       country,
                "distance_km":   0,
                "cumulative_km": round(total_km, 1),
                "method":        "city_db",
            })

        # ── ACTIVITY (이동)
        elif "activity" in record:
            act      = record["activity"]
            act_type = act.get("topCandidate", {}).get("type", "unknown")
            dist_km  = float(act.get("distanceMeters", 0)) / 1000
            total_km += dist_km
            end_geo   = act.get("end", "")
            start_geo = act.get("start", "")
            if not end_geo:
                continue
            elat, elon = parse_geo(end_geo)
            slat, slon = parse_geo(start_geo) if start_geo else (elat, elon)

            # ★ 비행: 공항 DB 우선 적용
            if act_type == "flying":
                arr = find_nearest_airport(elat, elon)
                dep = find_nearest_airport(slat, slon)
                if arr:
                    city, country, method = arr[4], arr[5], f"airport_db:{arr[2]}"
                    print(f"  [비행] {date_str} | "
                          f"{dep[4] if dep else '?'}({dep[2] if dep else '?'}) "
                          f"-> {city}({arr[2]}) | {dist_km:.0f}km")
                else:
                    city, country = lookup_city(elat, elon)
                    method = "city_db_fallback"
                dep_city    = dep[4] if dep else lookup_city(slat, slon)[0]
                dep_country = dep[5] if dep else lookup_city(slat, slon)[1]
                results.append({
                    "type":          "flying",
                    "date":          date_str,
                    "start_time":    record["startTime"],
                    "end_time":      record["endTime"],
                    "dep_city":      dep_city,
                    "dep_country":   dep_country,
                    "city":          city,
                    "country":       country,
                    "start_lat":     slat,
                    "start_lon":     slon,
                    "end_lat":       elat,
                    "end_lon":       elon,
                    "distance_km":   round(dist_km, 1),
                    "cumulative_km": round(total_km, 1),
                    "method":        method,
                })

            # 일반 이동: bounding box
            else:
                city, country = lookup_city(elat, elon)
                results.append({
                    "type":          act_type,
                    "date":          date_str,
                    "start_time":    record["startTime"],
                    "end_time":      record["endTime"],
                    "start_lat":     slat,
                    "start_lon":     slon,
                    "end_lat":       elat,
                    "end_lon":       elon,
                    "city":          city,
                    "country":       country,
                    "distance_km":   round(dist_km, 1),
                    "cumulative_km": round(total_km, 1),
                    "method":        "city_db",
                })

    print(f"파싱 완료: {len(results)}개 레코드, 총 거리 {total_km:,.1f} km\n")
    return results, total_km


# ══════════════════════════════════════════════════════════════
# 5. 날짜 필터
# ══════════════════════════════════════════════════════════════
def filter_by_date(results, end_date):
    """end_date(포함) 이후 레코드 제거. end_date가 None이면 필터 없음."""
    if end_date is None:
        return results
    filtered = [r for r in results if r["date"] <= end_date]
    removed  = len(results) - len(filtered)
    if removed:
        print(f"날짜 필터: {end_date} 이후 {removed}개 레코드 제거\n")
    return filtered


# ══════════════════════════════════════════════════════════════
# 6. Forward fill — Unknown 보간
#    city == "Unknown"인 레코드를 직전 Known 값으로 채움
#    (이동 중이거나 bounding box 바깥 좌표일 때 발생)
# ══════════════════════════════════════════════════════════════
def forward_fill(results):
    last_city, last_country = "Unknown", "Unknown"
    filled = 0
    for r in results:
        if r["city"] != "Unknown":
            last_city    = r["city"]
            last_country = r["country"]
        else:
            r["city"]    = last_city
            r["country"] = last_country
            r["method"]  = "forward_fill"
            filled += 1
    if filled:
        print(f"Forward fill: {filled}개 레코드 보간 완료\n")
    return results


# ══════════════════════════════════════════════════════════════
# 8. 실행 진입점
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Step 1. 파싱 + 도시 판별
    results, total_km = process_timeline(INPUT_PATH)

    # Step 2. 날짜 필터
    results = filter_by_date(results, END_DATE)

    # Step 3. Forward fill (Unknown 보간)
    results = forward_fill(results)

    # Step 4. 전체 결과 JSON 저장
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"전체 결과 JSON 저장 완료: {OUT_JSON} ({len(results)}개 레코드)\n")

    print(f"총 여행 거리: {total_km:,.1f} km")
