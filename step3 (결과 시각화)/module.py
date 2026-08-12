"""step3 지도 생성 보조 모듈: 시간 표기, TMAP 경유지 최적화 API 호출.

**엔드포인트는 `routeSequential100`이다.** 원래 쓰던 `routeSequential30`은
일일 호출 한도가 작아 3회차를 한 번 돌리면 소진된다(실측: QUOTA_EXCEEDED).
100은 경유지도 100개까지 받으므로, 클러스터 하나가 한 번의 호출로 끝난다
(회차당 대여소가 30개를 넘는 일이 없으므로 분할 호출도 사실상 사라진다).

호출 한도가 있는 유료 API이므로 두 가지 안전장치를 둔다.

  · **호출 예산** — 한 프로세스에서 MAX_CALLS(기본 25)회를 넘기지 않는다.
    넘으면 TmapBudgetExceeded를 올리고, 호출한 쪽이 직선 경로로 대체한다.
  · **한도 초과 즉시 중단** — 429 중 QUOTA_EXCEEDED는 재시도해도 소용없으므로
    (하루가 지나야 풀린다) 기다리지 않고 TmapQuotaExceeded를 올린다.

둘 다 지도 품질만 떨어뜨릴 뿐 파이프라인을 멈추지 않는다.
"""
import os
import time

import requests

# 한 프로세스에서 허용할 TMAP 호출 횟수.
# 정규 실행은 회차당 클러스터 10개 × 3회차 = 30건이므로 여유를 조금 두고 35로 잡았다.
# (경유지 100개까지 한 번에 보내므로 클러스터 하나당 정확히 1건이다.)
# 예산을 넘기면 남은 경로는 직선으로 그리고 실행은 계속한다.
MAX_CALLS = int(os.getenv("PBR_TMAP_MAX_CALLS", "35"))

# 최대 경유지 수 (routeSequential100 기준)
MAX_VIA = int(os.getenv("PBR_TMAP_MAX_VIA", "100"))

_call_count = 0


class TmapQuotaExceeded(RuntimeError):
    """TMAP 일일 호출 한도를 소진했다 (429 QUOTA_EXCEEDED)."""


class TmapBudgetExceeded(RuntimeError):
    """이 실행에 허용된 호출 예산(MAX_CALLS)을 다 썼다."""


def call_count() -> int:
    """이 프로세스가 지금까지 보낸 TMAP 호출 수."""
    return _call_count


def reset_call_count() -> None:
    _call_count = 0
    globals()["_call_count"] = 0


def seconds_to_hms(sec):
    """초 → 'HH:MM:SS'. 값이 없으면 None."""
    try:
        sec = int(sec)
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    except (TypeError, ValueError):
        return None


# ---------------------- Tmap ----------------------

def call_tmap_sequential(start, end, via_points, start_time="201709121938", headers=None, url=None,
                         retries=2, timeout=30):
    """TMAP 경유지 최적화 API 1회 호출. 429는 재시도, 4xx는 즉시 실패."""
    payload = {
        "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
        "startName": start["name"], "startX": str(start["X"]), "startY": str(start["Y"]),
        "startTime": start_time,
        "endName": end["name"], "endX": str(end["X"]), "endY": str(end["Y"]),
        "searchOption": "0", "carType": "4", "viaPoints": via_points,
    }
    global _call_count

    for attempt in range(retries + 1):
        if _call_count >= MAX_CALLS:
            raise TmapBudgetExceeded(
                f"TMAP 호출 예산 {MAX_CALLS}건을 다 썼습니다"
                f" (PBR_TMAP_MAX_CALLS로 조정)")
        try:
            _call_count += 1
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code == 429:
                # 일일 한도 초과는 기다려도 풀리지 않는다 — 즉시 포기한다.
                if "QUOTA_EXCEEDED" in r.text:
                    raise TmapQuotaExceeded(
                        f"TMAP 일일 호출 한도 소진 ({_call_count}번째 호출): {r.text[:200]}")
                time.sleep(1.5)     # 순간적인 속도 제한은 재시도할 값어치가 있다
                continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError:
            print(f"[HTTP {r.status_code}] {r.text[:300]}")
            if 400 <= r.status_code < 500:
                raise
            if attempt < retries:
                time.sleep(1.5)
        except requests.RequestException as e:
            print(f"[REQ] {e}")
            if attempt < retries:
                time.sleep(1.5)
    raise RuntimeError("Tmap API 호출 실패")


def call_tmap_chunked(start, end, via_points, headers=None, url=None, max_via=None):
    """경유지가 max_via를 넘으면 구간을 나눠 여러 번 호출한다.

    각 구간의 마지막 경유지를 그 구간의 도착지로 삼고,
    다음 구간의 출발지로 이어 붙인다. 반환: GeoJSON 응답 리스트(순서대로).

    max_via 기본값은 MAX_VIA(=100). routeSequential100을 쓰므로 현실적인
    클러스터 크기(최대 26곳)에서는 분할이 일어나지 않는다.
    """
    max_via = MAX_VIA if max_via is None else max_via

    if len(via_points) <= max_via:
        return [call_tmap_sequential(start, end, via_points, headers=headers, url=url)]

    geo_list = []
    current_start = start
    remaining = list(via_points)

    while remaining:
        chunk = remaining[:max_via]
        remaining = remaining[max_via:]

        if remaining:
            # 구간 도착지 = 이번 청크의 마지막 경유지
            last = chunk[-1]
            leg_end = {"name": last["viaPointName"], "X": last["viaX"], "Y": last["viaY"]}
            leg_vias = chunk[:-1]
        else:
            leg_end = end
            leg_vias = chunk

        geo_list.append(
            call_tmap_sequential(current_start, leg_end, leg_vias, headers=headers, url=url)
        )
        current_start = leg_end

    return geo_list


def extract_cumulative_times(geojson):
    """TMAP GeoJSON에서 선분(properties.time: 초)을 누적해
    각 포인트(S, V..., E) 도달 누적 시간을 계산한다.

    반환: {'point_coords': [(lat, lon, ptype, props), ...], 'elapsed_sec': [누적초 or None, ...]}
    일부 응답에 선분 time이 없으면 elapsed_sec는 None 리스트.
    """
    features = geojson.get("features", [])

    segment_times = []
    for f in features:
        if f.get("geometry", {}).get("type") == "LineString":
            t = f.get("properties", {}).get("time")
            segment_times.append(int(t) if t is not None else None)

    point_coords = []
    for f in features:
        if f.get("geometry", {}).get("type") == "Point":
            lon, lat = f["geometry"].get("coordinates", [None, None])
            ptype = f.get("properties", {}).get("pointType")
            point_coords.append((lat, lon, ptype, f.get("properties", {})))

    if not segment_times or any(t is None for t in segment_times):
        return {"point_coords": point_coords, "elapsed_sec": [None] * len(point_coords)}

    elapsed = [0]
    acc = 0
    for t in segment_times:
        acc += int(t)
        elapsed.append(acc)

    # 길이가 어긋나면 패딩/자르기
    if len(elapsed) < len(point_coords):
        elapsed += [elapsed[-1]] * (len(point_coords) - len(elapsed))
    elif len(elapsed) > len(point_coords):
        elapsed = elapsed[:len(point_coords)]
    return {"point_coords": point_coords, "elapsed_sec": elapsed}


def merge_tmap_results(geo_list):
    """분할 호출된 GeoJSON 결과들을 하나의 경로로 이어 붙인다.

    - features: 모든 응답의 feature를 순서대로 합침 (지도에 그대로 그림)
    - point_coords / elapsed_sec: 구간 경계의 중복 포인트를 제거하고
      누적 시간에 이전 구간의 총 시간을 더함.
      한 구간이라도 시간 정보가 없으면 전체 elapsed_sec는 None 리스트.
    """
    features = []
    point_coords = []
    elapsed_sec = []
    offset = 0
    timing_ok = True

    for i, geo in enumerate(geo_list):
        features.extend(geo.get("features", []))

        timing = extract_cumulative_times(geo)
        pts = timing["point_coords"]
        els = timing["elapsed_sec"]

        if i > 0:
            # 이전 구간의 도착점 == 이번 구간의 출발점 → 중복 제거
            pts = pts[1:]
            els = els[1:]

        if any(e is None for e in els):
            timing_ok = False

        point_coords.extend(pts)
        if timing_ok:
            shifted = [e + offset for e in els]
            elapsed_sec.extend(shifted)
            if shifted:
                offset = shifted[-1]

    if not timing_ok:
        elapsed_sec = [None] * len(point_coords)

    return {"features": features, "point_coords": point_coords, "elapsed_sec": elapsed_sec}
