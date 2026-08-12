# experiments/

파이프라인에 속하지 않는 검증·실험 스크립트 모음입니다.

## 파라미터 실험 (결과는 [../docs/EXPERIMENTS.md](../docs/EXPERIMENTS.md))

기본값으로 쓰이는 `z`와 `γ`는 아래 스크립트로 정했습니다. **값을 바꾸려면
같은 스크립트를 다시 돌려 근거를 남기세요.**

| 파일 | 묻는 것 | 결론 |
| --- | --- | --- |
| `z_sweep.py` | `z = 1.65`가 정말 95%를 덮나 | 92.5%뿐 → `TARGET_Z = 1.99` |
| `predictor_compare.py` | `_10_15`의 `mu`는 쓸모없나 | 측정이 틀렸다 (작업 대상만 보면 +40.2%) |
| `seasonal_window.py` | 계절 전환기를 어떻게 넘나 | 분석 달 첫 14일로 배율 보정 |

```powershell
python experiments/z_sweep.py            # 커버리지 vs 작업량
python experiments/predictor_compare.py  # 예측기 비교 + 무리별 진단
python experiments/seasonal_window.py    # 학습 창 비교
```

셋 다 DB의 `net_demand`를 읽으므로 **여러 달의 순수요가 적재돼 있어야** 합니다
(연속된 달이 최소 2개). 적재는 `tools/load_rentals.py --split-by-month` 참고.

## 학습용 스크립트

| 파일 | 내용 | 원래 위치 |
| --- | --- | --- |
| `pulp_test.py` | PuLP 라이브러리 튜토리얼 | `test/` |
| `pulp_test2.py` | PuLP 최소 예제 | `test/` |
| `matplotlib_month_graph.py` | 월별 대여량 그래프(더미 데이터, 한글 폰트 설정 예시) | `test/test.py` |
| `step0_rebal_qty_check.py` | rebal_qty 합계 확인용 (구식 duration `_05_15` 참조 — 실행하려면 수정 필요) | `step0 (raw데이터 처리)/test.py` |
| `step1_cluster_memo.py` | 클러스터링 실행 결과 메모 | `step1 (...)/test.py` |
