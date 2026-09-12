"""월별 원천 대여 이력 CSV를 1년치 파일로 병합하고 이상치를 제거한다.

- concat: data/raw_data/타슈 대여이력 정보(25.04~26.03)/ 하위 월별 파일 병합
- preprocess: 이용시간(분)·이용거리(km)의 IQR×1.5 밖 이상치 제거

⚠️ **이상치 제거는 원본을 덮어쓰지 않는다** (1.26.129). 예전에는 읽은 파일에
그대로 다시 썼는데, IQR은 **잘라 낸 뒤 다시 재면 좁아지므로 멱등이 아니다** —
같은 파일에 반복 적용하면 계속 깎인다. 50만 행 표본 실측:

    1회 통과 470,381행(94.1%) → 2회 453,559 → 3회 435,299 → 4회 424,911

**네 번 돌리면 원본의 15%가 사라진다.** 그런데 이 스크립트는 `--skip-eda` 없이
`python run_pipeline.py`를 치면 매번 실행되고, 대상은 1.5GB짜리 원천 이력이며
백업은 없다. 그래서 결과를 **별도 파일**(`… (이상치 제거).csv`)에 쓴다 —
입력이 늘 원본이므로 몇 번을 돌려도 같은 값이 나온다.

📌 **이 필터는 계획 경로에 걸리지 않는다 — 그리고 그것이 옳다고 판정됐다.**
순수요를 만드는 `pipeline/step0_collect/raw_to_net.py`는 시각·대여소ID 네 컬럼만 읽어서
이용시간·이용거리를 보지 않는다. 계획 경로로 옮겨야 하는지는 1.20.8에서 재고
**옮기지 않기로 결론이 났다** — 원천의 최댓값이 이미 46분·3.7km로 캡돼 있어
IQR 울타리가 자르는 것은 오류가 아니라 **정상 이용의 상위 4%** 였고, 옮기면
작업 대상이 13.4% 뒤바뀐다(docs/분석/DECISIONS.md 6-1,
experiments/structure/outlier_impact.py).

그래서 이 산출물은 **EDA·문서용이다.** 계획은 이 파일을 읽지 않는다.

실행 예:
    python "pipeline/step0_eda/concat_1year_file.py" --concat --preprocess
    python "pipeline/step0_eda/concat_1year_file.py" --preprocess
옵션 없이 실행하면(파이프라인 기본) 병합 파일이 있을 때만 이상치 제거를 수행하고,
없으면 건너뛴다(전체 파이프라인 중단 방지).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from project_config import DATA_ROOT, PROJECT_ROOT

path = str(DATA_ROOT / "raw_data/타슈 대여이력 정보(25.04~26.03)/대전시 공영자전거 타슈 대여이력 정보({period}).csv")
result_file = str(DATA_ROOT / "raw_data/타슈 대여이력(25.04~26.03).csv")
# 이상치를 제거한 결과. **원본과 다른 파일이어야 한다** — 위 docstring 참고.
cleaned_file = str(DATA_ROOT / "raw_data/타슈 대여이력(25.04~26.03) (이상치 제거).csv")


def concat_file(period: list):
    df_list = []

    for p in period:
        file_path = path.format(period=p)
        print(f"읽는 중: {p}")

        try:
            df = pd.read_csv(file_path, encoding='utf-8-sig')
            print("utf-8-sig 성공")
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding='cp949')
            print("cp949 성공")

        print(f"{p}의 shape : {df.shape}")
        df_list.append(df)
        print("-" * 50)

    final_df = pd.concat(df_list, ignore_index=True)

    print()
    print(f"최종 파일 shape : {final_df.shape}")
    print(f"최종 파일 head : {final_df.head()}")

    final_df.to_csv(result_file, index=False, encoding='utf-8')
    print(f"{result_file}을 저장했습니다.")


def preprocessing(file_path: str, out_path: str = None):
    """IQR×1.5 밖을 잘라 **다른 파일에** 쓴다.

    `out_path`를 주지 않으면 `cleaned_file`이다. **`file_path`와 같은 경로를
    주지 마라** — 그러면 다음 실행이 이미 깎인 것을 또 깎는다(멱등 아님).
    """
    out_path = out_path or cleaned_file
    if Path(out_path) == Path(file_path):
        raise SystemExit(
            "이상치 제거 결과를 입력과 같은 파일에 쓸 수 없습니다.\n"
            "  IQR은 잘라 낸 뒤 다시 재면 좁아져, 돌릴 때마다 원본이 더 깎입니다.\n"
            f"  입력: {file_path}")

    df = pd.read_csv(file_path, encoding='utf-8')

    print(f"전처리 전의 형태 : {df.shape}")
    target_col = ['이용시간(분)', '이용거리(km)']

    print('-' * 50)

    for t in target_col:
        Q3 = df[t].quantile(0.75)
        Q1 = df[t].quantile(0.25)

        IQR = Q3 - Q1
        print(f"IQR({t}) : {IQR}")

        lower = Q1 - 1.5 * IQR
        upper = Q3 + 1.5 * IQR

        df = df[(df[t] >= lower) & (df[t] <= upper)]
        print(f"df[{t}]의 이상값을 제거한 후의 shape : {df[t].shape}")
        print('-' * 50)

    print(f"전처리 후의 형태 : {df.shape}")

    df.to_csv(out_path, index=False, encoding='utf-8')
    print(f"전처리 후, {out_path}을 저장했습니다.")
    print(f"  원본 {file_path}은(는) 그대로 둡니다 — 다시 돌려도 같은 결과가 나옵니다.")
    print("  ⚠ 이 파일은 계획 경로가 읽지 않습니다 (raw_to_net.py는 시각·대여소ID만"
          " 읽습니다). 이상치를 걸어야 하는지는 experiments/structure/outlier_impact.py 참고.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--concat", action="store_true", help="월별 파일 병합 실행")
    parser.add_argument("--preprocess", action="store_true", help="이상치 제거 실행")
    args, _ = parser.parse_known_args()

    period = [
        "25년04월", "25년05월", "25년06월", "25년07월",
        "25년08월", "25년09월", "25년10월", "25년11월",
        "25년12월", "26년01월", "26년02월", "26년03월"
    ]

    if args.concat:
        concat_file(period)

    if args.preprocess:
        preprocessing(result_file)
    elif not args.concat:
        # 옵션 없이 실행된 경우(파이프라인 기본): 병합 파일이 있으면 이상치 제거, 없으면 건너뜀
        if Path(result_file).exists():
            preprocessing(result_file)
        else:
            print(f"병합 파일이 없어 건너뜁니다: {result_file}")
