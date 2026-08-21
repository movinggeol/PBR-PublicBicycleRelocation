import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import folium
from folium import FeatureGroup
from folium.plugins import FeatureGroupSubGroup
import pandas as pd
import numpy as np

from project_config import PROJECT_ROOT, duration_list, ensure_output_dirs, get_runtime_config

# read_csv
clustered_file = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")
cluster_center_file = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top_center{duration} ({now}).csv")

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
            tiles="CartoDB positron"  # 밝은 배경의 깔끔한 지도
        )

        colors = ['red', 'orange', 'yellow', 'green',    #3
                'blue', 'navy', 'purple', 'Magenta',    #7
                'black', 'Teal', 'Cyan', 'darkred',     #11
                'Salmon', 'Lime', 'gold', 'gray',       #15
                'Olive', 'Indigo', 'Brown'              #18
                ]

        unique_clusters = sorted(pick_drop['cluster'].unique())
        
        # Cluster + Pick/Drop 그룹 생성
        layer_dict = {}
        
        # cluster별 Layer 생성
        for c in unique_clusters:
            drop_layer = folium.FeatureGroup(
                name=f"Cluster {c} - Drop",
                show=True
            )
            drop_layer.add_to(m)

            pick_layer = folium.FeatureGroup(
                name=f"Cluster {c} - Pick",
                show=True
            )
            pick_layer.add_to(m)

            layer_dict[(c, 'drop')] = drop_layer
            layer_dict[(c, 'pick')] = pick_layer

        for _, row in pick_drop.iterrows(): 
            
            cluster = row['cluster']
            rebal = row['rebal_qty']
            
            if rebal > 0:
                status = 'Drop (분배)'
                layer = layer_dict[(cluster, 'drop')]
            else:
                status = 'Pick (회수)'
                layer = layer_dict[(cluster, 'pick')]
            
            radius = max(5, abs(rebal)*0.3)

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

                color=colors[cluster % len(colors)],    # cluster : int
                weight=2,
                opacity=1.0,
                
                fill=True,
                fill_color=colors[cluster % len(colors)],
                fill_opacity=0.5,

                tooltip=tooltip
            ).add_to(layer)

        '''
        # 클러스터 중심 표시
        center_df = pd.read_csv(cluster_center_file.format(duration=duration), encoding='utf-8')
        for _, c in center_df.iterrows():
            folium.Marker(
                location=[c['lon'], c['lat']],
                icon=folium.Icon(color='black', icon='star'),
                tooltip=f"Cluster {c['cluster']} Center"
            ).add_to(m)
        '''
        
        folium.LayerControl(collapsed=False).add_to(m)

        m.save(clusterd_map.format(duration=duration, now=now))
        print(f"folium map 저장 완료({duration})\n")



if __name__ == '__main__':

    ensure_output_dirs()
    make_clustered_map(duration_list(config))