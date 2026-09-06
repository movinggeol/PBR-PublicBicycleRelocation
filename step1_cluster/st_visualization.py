import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import folium
from folium import FeatureGroup
from folium.plugins import FeatureGroupSubGroup
import pandas as pd
import numpy as np

from mapviz import (DROP_LABEL, PICK_LABEL, cluster_color, legend_html,
                    qty_radius, swatch_circle, swatch_size_scale)
from project_config import (
    MAP_TILES, PROJECT_ROOT, VEHICLE_CAPACITY,
    duration_list, ensure_output_dirs, get_runtime_config,
)

# read_csv
clustered_file = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")

# to_csv
clusterd_map = str(PROJECT_ROOT / "data/pp_data/ILP/visualization/clusterd_map{duration} ({now}).html")

config = get_runtime_config()
now = config.now

def make_clustered_map(durations: list):

    for duration in durations:
        pick_drop = pd.read_csv(
            clustered_file.format(duration=duration, now=now), 
            low_memory=False, 
            encoding='utf-8'
        )

        center_lat = pick_drop['lat'].mean()
        center_lon = pick_drop['lon'].mean()
        print(f"map 중심점 : {center_lat, center_lon}")

        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=13,
            control_scale=True,
            tiles=MAP_TILES          # 세 지도가 같은 배경을 써야 한다(project_config)
        )

        # 색은 mapviz.cluster_color()가 정한다 — 세 지도가 한 벌을 쓴다.
        # 예전 18색 목록은 red/darkred/Salmon, green/Olive/Lime처럼 인접한
        # 색이 많아 군집이 겹치면 구분이 어려웠다(1.26.75 조사 → 1.26.80).
        unique_clusters = sorted(pick_drop['cluster'].unique())
        
        # 군집 × (내리기·싣기) 그룹. 이름은 한글로 둔다 — 지시서는
        # 싣기/내리기인데 여기만 Pick/Drop이면 한 개념에 용어가 두 벌이다
        # (1.26.107). 레이어 컨트롤은 지도 위에 겹쳐 뜨므로 이름이 짧아야
        # 한다 — 군집 19개면 줄이 38개다.
        layer_dict = {}
        
        # cluster별 Layer 생성
        for c in unique_clusters:
            drop_layer = folium.FeatureGroup(
                name=f"군집 {c} · 내리기",
                show=True
            )
            drop_layer.add_to(m)

            pick_layer = folium.FeatureGroup(
                name=f"군집 {c} · 싣기",
                show=True
            )
            pick_layer.add_to(m)

            layer_dict[(c, 'drop')] = drop_layer
            layer_dict[(c, 'pick')] = pick_layer

        for _, row in pick_drop.iterrows(): 
            
            cluster = row['cluster']
            rebal = row['rebal_qty']
            
            # 낱말은 mapviz.py 한 벌에서 온다. 예전에는 같은 문구를 여기에
            # 손으로 적어 두었는데, mapviz의 주석은 그 상수가 **step1 툴팁과
            # 같은 말을 쓰려고** 있다고 말하면서 정작 잇지는 않았다 — 한쪽만
            # 고치면 지도와 범례가 다른 말을 하게 된다(1.26.113).
            if rebal > 0:
                status = DROP_LABEL
                layer = layer_dict[(cluster, 'drop')]
            else:
                status = PICK_LABEL
                layer = layer_dict[(cluster, 'pick')]
            
            # 크기 규칙도 mapviz 한 벌에서 온다 — 아래 범례의 눈금과 **같은
            # 함수**로 그려야 눈금이 실제 마커와 맞는다(1.26.129).
            radius = qty_radius(rebal, VEHICLE_CAPACITY)

            tooltip = f"""
            <b>{row['station_name']}</b><br>
            유형: {status}<br>
            재배치 수량: {rebal}<br>
            현재 재고: {row['stock']}<br>
            클러스터: {cluster}<br>
            Target: {row['target_qty']:.2f}<br>
            mu: {row['mu']:.2f}, sigma: {row['sigma']:.2f}
            """

            folium.CircleMarker(
                location=[row['lat'], row['lon']],
                radius=radius,

                color=cluster_color(cluster),    # cluster : int
                weight=2,
                opacity=1.0,

                fill=True,
                fill_color=cluster_color(cluster),
                fill_opacity=0.5,

                # sticky: 풍선이 커서를 따라온다. 점이 촘촘한 곳에서
                # 어느 점의 설명인지 헷갈리지 않는다 (세 지도가 같게).
                tooltip=folium.Tooltip(tooltip, sticky=True)
            ).add_to(layer)

        # 군집 중심(메도이드) 마커는 그리지 않는다. 예전에 주석으로 남아 있던
        # 코드가 읽던 top_center*.csv는 **어느 단계도 만들지 않는 파일**이었다.
        # 되살리려면 파일을 만드는 쪽부터 필요하고, webapp의 후보 파일 글롭이
        # 그 파일까지 잡지 않는지도 함께 봐야 한다(store.CSV_FALLBACK).

        # 범례. 이 지도는 원래 범례가 **아예 없어서**, 18색으로 군집을 나눠
        # 놓고 어느 색이 몇 번인지 알 방법이 레이어 컨트롤의 이름을 읽는 것
        # 뿐이었다(1.26.75 조사 → 1.26.80 채택).
        #
        # ⚠️ 군집 목록은 접는다. 18개를 한 줄씩 세우면 범례가 610px, 화면
        #    세로의 2/3를 먹는다(실측). 레이어 컨트롤이 이미 군집을 하나씩
        #    껐다 켤 수 있으므로, 펴 두면 같은 정보가 두 벌 보인다.
        m.get_root().html.add_child(folium.Element(legend_html(
            "범례 — 군집",
            [(swatch_circle(cluster_color(c), str(c)), f"군집 {c}")
             for c in unique_clusters],
            collapse_after=0, collapse_label="군집",
            # ⚠️ 크기로 값을 말하면 **눈금을 함께 낸다** (1.26.107이 step4에
            # 세운 규칙인데 이 지도만 빠져 있었다). 눈금이 없으면 "저것보다
            # 크다"까지만 읽히고, 정작 묻고 싶은 "몇 대인가"는 점을 하나씩
            # 눌러 봐야 한다.
            note="원 크기는 재배치 수량입니다."
                 + swatch_size_scale(
                     [qty_radius(q, VEHICLE_CAPACITY) for q in (3, 6, 9)],
                     ["3대", "6대", "9대"])
                 + "<br>점에 커서를 대면 자세한 값이 뜹니다.")))

        # ⚠️ 레이어 컨트롤은 지도 **위에** 겹쳐 뜬다. 펴 두면 군집 수만큼
        # 줄이 서서 지도 오른쪽을 위에서 아래까지 덮는다 — 군집 19개짜리
        # 산출물에서 38줄, 780px였다(실측 1.26.107). 접어 둔다: 색이 무슨
        # 뜻인지는 이제 **범례**가 말하고, 컨트롤은 걸러 보는 도구다.
        folium.LayerControl(collapsed=True).add_to(m)

        m.save(clusterd_map.format(duration=duration, now=now))
        print(f"folium map 저장 완료({duration})\n")



if __name__ == '__main__':

    ensure_output_dirs()
    make_clustered_map(duration_list(config))