import os, re, ast, json, time, requests, pandas as pd, folium
from pathlib import Path
from dotenv import load_dotenv


'''
# ---------------------- 경로 설정 ----------------------
routes_csv = Path("data/vrp_multi_routes_all_frommetrics_v3_h15.csv")
coords_csv  = Path("data/tashu_station_net_metrics.csv")
plan_csv    = Path("data/ilp_allocation_plan_v3_h15.csv")

out_html    = Path("클러스터_순차방문_경로맵_tmap_pickdrop.html")
'''

# ---------------------- 공통 유틸 ----------------------

'''


def read_csv_any_encoding(path, encs=("utf-8-sig","utf-8","cp949","euc-kr")):   # 파일 인코딩용
    last=None
    for e in encs:
        try:
            return pd.read_csv(path, low_memory=False, encoding=e)
        except Exception as err:
            last=err
    raise last

def clean_id(x:str)->str:
    return str(x).strip().upper()

def strip_leading_index(name:str)->str: # 정규표현식 패턴과 매칭되는 부분("[0], [ 12 ] 등")을 ''로 대체함.
    if not name: return ""
    # re.sub(pattern, replace, origina_string)
    return re.sub(r'^\s*\[\s*\d+\s*\]\s*','',str(name)).strip()

'''

def seconds_to_hms(sec):    # 누적 시간 출력용(초 -> %d시:%d분,%d초)
    try:
        sec = int(sec)
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return None



'''

# ---------------------- 좌표/경로/계획 정규화 ----------------------
def normalize_coords(df: pd.DataFrame):
    # 허용 컬럼명: station_id | station_key,  lat | latitude | y,  lon | longitude | x,  name | station_name(옵션)
    cols = {c.lower(): c for c in df.columns}
    sid = cols.get('station_id') or cols.get('station_key')
    name= cols.get('station_name') or cols.get('name')
    lat = cols.get('latitude') or cols.get('lat') or cols.get('y')
    lon = cols.get('longitude') or cols.get('lon') or cols.get('x')
    if not sid or not lat or not lon:
        raise ValueError(f"좌표 파일에 station_id(또는 station_key)/lat(or latitude)/lon(or longitude) 컬럼이 필요합니다. 실제: {df.columns.tolist()}")
    use = [sid, lat, lon] + ([name] if name else [])
    slim = df[use].copy()
    slim.columns = ['station_id','latitude','longitude'] + (['station_name'] if name else [])
    slim['station_id'] = slim['station_id'].astype(str).str.strip().str.upper()
    return slim

def normalize_routes(df: pd.DataFrame):
    cols = {c.lower(): c for c in df.columns}
    nodes_col = (cols.get('nodes') or cols.get('route') or cols.get('path') or cols.get('sequence')
                 or cols.get('stations') or cols.get('station_ids'))
    if not nodes_col:
        # 패턴 추정
        cands=[]
        for c in df.columns:
            try:
                r = df[c].astype(str).str.contains(r'\[.*\]|>|,|\|', regex=True).mean()
            except Exception:
                r = 0
            if r > 0.5: cands.append(c)
        nodes_col = cands[0] if cands else None
    if not nodes_col:
        raise ValueError(f"'nodes' 유사 열을 찾지 못했습니다. 실제: {df.columns.tolist()}")

    cluster_col = (cols.get('cluster') or cols.get('cluster_id'))
    if not cluster_col:
        df['_cluster_auto_'] = range(1, len(df)+1)
        cluster_col = '_cluster_auto_'
    return nodes_col, cluster_col

def parse_nodes(cell):
    if pd.isna(cell): return []
    s = str(cell).strip()
    # Python list
    try:
        if s.startswith('[') and s.endswith(']'):
            v = ast.literal_eval(s)
            if isinstance(v,list): return [str(x).strip() for x in v]
    except: pass
    # JSON list
    try:
        v = json.loads(s)
        if isinstance(v,list): return [str(x).strip() for x in v]
    except: pass
    # delimiters
    for sep in ['>', ',', '|', ' ', '→', '->', '—']:
        if sep in s:
            return [p for p in (x.strip() for x in s.split(sep)) if p]
    return [s]

def strip_depots(seq):
    arr = [clean_id(a) for a in seq if str(a).strip()!=""]
    while arr and arr[0]=='0': arr.pop(0)
    while arr and arr[-1]=='0': arr.pop()
    return arr

def normalize_plan(df: pd.DataFrame):
    """
    계획파일: pick_station_id, drop_station_id, qty (정수)
    클러스터/타임슬라이스가 있으면 추가로 인식(있어도 없어도 동작).
    """
    cols = {c.lower(): c for c in df.columns}
    pc = cols.get('pick_station_id') or cols.get('pick')
    dc = cols.get('drop_station_id') or cols.get('drop')
    qc = cols.get('qty') or cols.get('quantity')
    cluster_c = cols.get('cluster') or cols.get('cluster_id')
    if not pc or not dc or not qc:
        raise ValueError(f"계획 파일에 pick_station_id/drop_station_id/qty 컬럼이 필요합니다. 실제: {df.columns.tolist()}")
    out = df[[pc,dc,qc] + ([cluster_c] if cluster_c else [])].copy()
    out.columns = ['pick_station_id','drop_station_id','qty'] + (['cluster'] if cluster_c else [])
    out['pick_station_id'] = out['pick_station_id'].astype(str).str.strip().str.upper()
    out['drop_station_id'] = out['drop_station_id'].astype(str).str.strip().str.upper()
    out['qty'] = pd.to_numeric(out['qty'], errors='coerce').fillna(0).astype(int)
    if 'cluster' in out.columns:
        out['cluster'] = out['cluster'].astype(str)
    return out

'''
    


# ---------------------- Tmap ----------------------
def call_tmap_sequential(start, end, via_points, start_time="201709121938", headers=None, url=None,
                         retries=2, timeout=30):
    payload = {
        "reqCoordType":"WGS84GEO","resCoordType":"WGS84GEO",
        "startName":start["name"],"startX":str(start["X"]),"startY":str(start["Y"]),
        "startTime":start_time,
        "endName":end["name"],"endX":str(end["X"]),"endY":str(end["Y"]),
        "searchOption":"0","carType":"4","viaPoints":via_points
    }
    for attempt in range(retries+1):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.5); continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError:
            print(f"[HTTP {r.status_code}] {r.text[:300]}")
            if 400 <= r.status_code < 500:
                raise
            if attempt < retries: time.sleep(1.5)
        except requests.RequestException as e:
            print(f"[REQ] {e}")
            if attempt < retries: time.sleep(1.5)
    raise RuntimeError("Tmap API 호출 실패")

def extract_cumulative_times(geojson):
    """
    Tmap GeoJSON에서 선분(properties.time: 초)을 누적해서
    각 포인트 인덱스(S, V..., E)에 도달하기까지의 누적 시간을 리스트로 리턴.
    반환: dict with keys:
      - 'point_coords': [(lat,lon,ptype,props), ...]  포인트 등장 순
      - 'elapsed_sec':  [누적초 또는 None,...]        포인트별
    주의: 일부 응답에는 선분 time이 없을 수 있음 → None 리스트 반환
    """
    features = geojson.get("features", [])
    # 선분 시간 수집
    segment_times = []
    for f in features:
        if f.get("geometry",{}).get("type") == "LineString":
            t = f.get("properties", {}).get("time")
            segment_times.append(int(t) if t is not None else None)

    # 포인트 순서 수집
    point_coords = []
    for f in features:
        if f.get("geometry",{}).get("type") == "Point":
            lon, lat = f["geometry"].get("coordinates",[None,None])
            ptype = f.get("properties",{}).get("pointType")
            point_coords.append((lat, lon, ptype, f.get("properties",{})))

    # 선분 수 == 포인트 수 - 1 인 경우가 이상적
    if not segment_times or any(t is None for t in segment_times):
        return {"point_coords": point_coords, "elapsed_sec": [None]*len(point_coords)}

    # 누적합
    elapsed = [0]  # 첫 포인트는 0초
    acc = 0
    for t in segment_times:
        acc += int(t)
        elapsed.append(acc)
    # 안전: 길이가 다르면 패딩/자르기
    if len(elapsed) < len(point_coords):
        elapsed += [elapsed[-1]] * (len(point_coords)-len(elapsed))
    elif len(elapsed) > len(point_coords):
        elapsed = elapsed[:len(point_coords)]
    return {"point_coords": point_coords, "elapsed_sec": elapsed}
