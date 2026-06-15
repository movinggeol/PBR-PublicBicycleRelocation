from datetime import datetime
import folium
import pandas as pd
import numpy as np

file_path = "data/pp_data/ILP/후보/top{duration} ({now}).csv"

result_file_path = "data/pp_data/성능 지표/verification{duration} ({now}).csv"
map_file_path = "data/pp_data/성능 지표/visualization/imbalance_map{duration} ({now}).html"

now = "2026-05-21 18"
#now = datetime.now().strftime('%Y-%m-%d %H')

def demand_satisfaction(reloc: pd.DataFrame):
    '''
    '수요 충족률' 계산 : 평균 순수요(mu) 대비 얼마나 수요를 충족할 수 있었는지
    imbalance를 통해 재배치 '전'보다 '후'가 얼마나 목표 상태(target_qty)에 가까워졌는가를 측정 
    imbalance : 부족과 과잉을 나누지 않고 하나의 수치로 통합함 (절대값 활용)
    '''

    temp = reloc.iloc[:, [0,1,2,3,10,5,8,9,6,7]].copy()
    print(temp.head())

    # 재배치 후 재고 = 기존 재고 + 재배치 수량
    temp['new_stock'] = temp['stock'] + temp['rebal_qty']

    # 불균형(imbalance) : 현재 재고(stock/new_stock)가 이상적인 목표 재고(target_qty)에서 얼마나 벗어나 있는가 (수량)
    temp['bf_imbalance'] = abs(temp['stock'] - temp['target_qty'])
    temp['af_imbalance'] = abs(temp['new_stock'] - temp['target_qty'])

    # 개선량 (imbalance는 절대값이라 bf가 더 큰 값일 테니 af를 뺌) (rebal_qty의 절대값과 같음)
    temp['improvement'] = temp['bf_imbalance'] - temp['af_imbalance']

    # 개선률
    temp['improvement_rate'] = (temp['improvement'] / temp['bf_imbalance']).round(2)
    avg_imp_rate = temp['improvement_rate'].mean(axis=0).round(2)
    pick_avg_imp_rate = temp.loc[temp['rebal_qty'] < 0, 'improvement_rate'].mean(axis=0).round(2)
    drop_avg_imp_rate = temp.loc[temp['rebal_qty'] > 0, 'improvement_rate'].mean(axis=0).round(2)


    print("-"*50)
    print(f"평균 개선률(improvement_rate) : {(100 * avg_imp_rate).round(2)}%")
    print(f"   - Pick 대상 대여소 평균 개선률: {(100 * pick_avg_imp_rate).round(2)}%")
    print(f"   - Drop 대상 대여소 평균 개선률: {(100 * drop_avg_imp_rate).round(2)}%")
    print("-"*50)


    
    temp.fillna(np.nan)
    
    return temp


def demand_satisfaction_map(reloc_df: pd.DataFrame, imbalance_df: pd.DataFrame):

    center_lat = reloc_df['lat'].mean()
    center_lon = reloc_df['lon'].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13,
        control_scale=True,
        title='CartoDB positron'
    )

    unique_clusters = sorted(imbalance_df['cluster'].unique())
    

    for idx, c in enumerate(unique_clusters):
        
        fg = folium.FeatureGroup(
            name=f"Cluster {c}", 
            show=True
        )
        fg.add_to(m)

        cluster_df = imbalance_df[imbalance_df['cluster']==c].copy()

        for _, row in cluster_df.iterrows():
            
            # Pick/Drop 색상
            if row['rebal_qty'] > 0:
                color = 'red'
                status = 'Drop'
            else:
                color = 'blue'
                status = 'Pick'
            
            # 작업 후 불균형 '개선률(improvement_rate)' 기반 마커
            radius = max(3, row['improvement'])
            opacity = (0.2 + 0.5*row['improvement_rate'])

            tooltip = f"""
            <b>{row['station_name']}</b><br>
            Cluster : {row['cluster']}<br>
            작업 유형 : {status}<br>
            재배치 수량 : {row['rebal_qty']}<br>
            작업 전 재고 : {row['stock']}<br>
            작업 후 재고 : {row['new_stock']}<br>
            목표 재고 : {row['target_qty']}<br>
            Before imbalance : {row['bf_imbalance']:.2f}<br>
            After imbalance : {row['af_imbalance']:.2f}<br>
            개선량 : {row['improvement']:.2f}<br>
            개선률 : {row['improvement_rate']*100:.1f}%
            """

            folium.CircleMarker(
                location=[row['lat'], row['lon']],
                radius=radius,

                color=color,
                weight=2,
                opacity=1.0,

                fill=True,
                fill_color=color,
                fill_opacity=opacity,

                tooltip=tooltip
            ).add_to(fg)

    legend_html = """
    <div style="
        position : fixed;
        bottom : 30px;
        left : 30px;
        z-index : 9999;
        background : white;
        padding : 10px;
        border : 2px solid gray;
    ">
    <b>Legend</b><br>

    🔴 Drop :
    부족 해소<br>

    🔵 Pick :
    과잉 해소<br><br>

    원 크기 :
    불균형 해소량<br>

    원 투명도 :
    개선률
    </div>
    """

    m.get_root().html.add_child(
        folium.Element(legend_html)
    )

    folium.LayerControl(
        collapsed=False
    ).add_to(m)


    m.save(map_file_path.format(duration=duration, now=now))

    print("지도 생성 완료")
    print("map_file_path  파일이 저장되었습니다.", (map_file_path.format(duration=duration, now=now)))




if __name__ == "__main__":
    duration_list = ['_05_10']
    #duration_list = ['_05_10', '_10_15', '_15_20', '_20_05']

    for duration in duration_list:
        reloc_df = pd.read_csv(file_path.format(duration=duration, now=now), encoding='utf-8')
        print(reloc_df.head())

        imbalance_df = demand_satisfaction(reloc_df).copy()
        #print(reloc_df.head())

        #demand_satisfaction_map(reloc_df, imbalance_df)


        #reloc_df.to_csv(result_file_path.format(duration=duration, now=now), index=False, encoding='utf-8')
        #print(f"\ result_file_path 파일이 저장되었습니다. ({result_file_path.format(duration=duration, now=now)})")