import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import folium
import pandas as pd

import db
from project_config import (
    PROJECT_ROOT, TIME_BUDGET_MINUTES, duration_list, ensure_output_dirs, get_runtime_config,
)

file_path = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")
vrp_plan_file = str(PROJECT_ROOT / "data/pp_data/VRP/VRP_plan{duration} ({now}).csv")

result_file_path = str(PROJECT_ROOT / "data/pp_data/성능 지표/verification{duration} ({now}).csv")
route_summary_file = str(PROJECT_ROOT / "data/pp_data/성능 지표/route_summary{duration} ({now}).csv")
map_file_path = str(PROJECT_ROOT / "data/pp_data/성능 지표/visualization/imbalance_map{duration} ({now}).html")

config = get_runtime_config()
now = config.now

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

    return temp


def route_summary(duration: str):
    '''
    VRP 결과(거리·시간 컬럼 포함)로 클러스터별 총 이동거리·운행시간을 집계한다.
    VRP 파일이 없거나 구버전(시간 컬럼 없음)이면 건너뛴다.
    '''
    path = Path(vrp_plan_file.format(duration=duration, now=now))
    if not path.exists():
        print(f"VRP 결과가 없어 경로 요약을 건너뜁니다: {path}")
        return

    vrp = pd.read_csv(path, encoding='utf-8')
    if 'distance_km' not in vrp.columns:
        print("VRP 결과에 거리·시간 컬럼이 없습니다(구버전). step2 vrp.py를 다시 실행하세요.")
        return

    # 방문수는 depot 복귀를 빼고 실제로 들른 대여소 수,
    # 처리대수는 pick 기준(pick+drop을 더하면 한 대를 두 번 세어 2배가 된다).
    # step2의 vehicle_assignment와 같은 기준이라 두 산출물의 숫자가 맞는다.
    visited = vrp[vrp['action'] != 'return']
    summary = visited.groupby('cluster').agg(방문수=('to_id', 'nunique')).reset_index()

    moved = (vrp[vrp['action'] == 'pick'].groupby('cluster')['qty']
             .sum().rename('처리대수').reset_index())

    totals = vrp.groupby('cluster').agg(
        총이동거리_km=('distance_km', 'sum'),
        총이동시간_분=('travel_sec', 'sum'),
        총작업시간_분=('work_sec', 'sum'),
        총소요시간_분=('cum_sec', 'max'),
    ).reset_index()

    summary = summary.merge(moved, on='cluster', how='left').merge(totals, on='cluster', how='left')
    summary['처리대수'] = summary['처리대수'].fillna(0).astype(int)
    summary = summary[['cluster', '방문수', '처리대수', '총이동거리_km',
                       '총이동시간_분', '총작업시간_분', '총소요시간_분']]

    for col in ['총이동시간_분', '총작업시간_분', '총소요시간_분']:
        summary[col] = (summary[col] / 60).round(1)
    summary['총이동거리_km'] = summary['총이동거리_km'].round(2)

    print("-" * 50)
    print("클러스터별 경로 요약:")
    print(summary.to_string(index=False))

    within = (summary['총소요시간_분'] <= TIME_BUDGET_MINUTES).sum()
    rate = within / len(summary) * 100 if len(summary) else 0
    print(f"전체: {summary['총이동거리_km'].sum():.2f} km, "
          f"최장 소요 {summary['총소요시간_분'].max():.1f} 분")
    print(f"시간 예산({TIME_BUDGET_MINUTES:.0f}분) 준수: "
          f"{within}/{len(summary)} 클러스터 ({rate:.0f}%)")
    if within < len(summary):
        print("  ⚠ 초과한 클러스터는 수요 예측 시간대가 지나간 뒤 작업이 끝납니다.")
    print("-" * 50)

    summary.to_csv(route_summary_file.format(duration=duration, now=now), index=False, encoding='utf-8')
    print(f"route_summary 파일이 저장되었습니다. ({route_summary_file.format(duration=duration, now=now)})")

    # CSV·DB 이중 기록 (DB_PLAN 2단계). 한글 컬럼은 db가 ASCII로 변환한다.
    db.save_output("route_summary", summary, run_label=now, duration=duration)


def demand_satisfaction_map(reloc_df: pd.DataFrame, imbalance_df: pd.DataFrame, duration: str):

    center_lat = reloc_df['lat'].mean()
    center_lon = reloc_df['lon'].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13,
        control_scale=True,
        tiles='CartoDB positron'
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
    ensure_output_dirs()

    for duration in duration_list(config):
        candidates = Path(file_path.format(duration=duration, now=now))
        if not candidates.is_file():
            # step1이 '대상 없음'으로 건너뛴 시간대.
            print(f"\n[건너뜀] {duration}: 후보 파일이 없습니다 ({candidates.name})")
            continue

        reloc_df = pd.read_csv(candidates, encoding='utf-8')
        print(reloc_df.head())

        imbalance_df = demand_satisfaction(reloc_df).copy()

        demand_satisfaction_map(reloc_df, imbalance_df, duration)

        imbalance_df.to_csv(result_file_path.format(duration=duration, now=now), index=False, encoding='utf-8')
        print(f"\nresult_file_path 파일이 저장되었습니다. ({result_file_path.format(duration=duration, now=now)})")

        # CSV·DB 이중 기록 (DB_PLAN 2단계). CSV가 아직 정본이다.
        db.save_output("metrics", imbalance_df, run_label=now,
                       period=config.period, duration=duration)

        route_summary(duration)