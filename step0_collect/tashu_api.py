"""TASHU Open API에서 대여소별 현재 재고를 수집한다.

출력: data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv
now는 project_config의 분석 시점 라벨을 사용한다(파이프라인 전체가 같은 라벨 공유).

**받아 오는 일 자체는 루트의 tashu.py가 한다.** 웹의 실시간 재고 대조가 같은
클라이언트를 쓰므로, 컬럼 규약(특히 x_pos=위도)이 두 곳으로 갈리지 않는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
import tashu
from project_config import PROJECT_ROOT, ensure_output_dirs, get_runtime_config

# to_csv
out_file_path = str(PROJECT_ROOT / "data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv")


def main() -> None:
    config = get_runtime_config()

    try:
        df = tashu.fetch_stations()
    except tashu.TashuError as err:
        raise SystemExit(str(err))
    print("타슈 대여소 api 호출 완료!")

    ensure_output_dirs()
    df.to_csv(out_file_path.format(now=config.now), encoding='utf-8', index=False)
    print(f"\n{out_file_path.format(now=config.now)} 가 저장되었습니다.")

    # CSV·DB 이중 기록 (DB_PLAN 2단계). CSV가 아직 정본이다.
    db.save_output("station_stock", df, run_label=config.now, period=config.period)


if __name__ == '__main__':
    main()
