from datetime import datetime
import pandas as pd


file_path = "data/pp_data/재배치 정보/rebal_qty{duration} ({now}).csv"

duration = '_05_15'
now = datetime.now().strftime('%Y-%m-%d %H')
print(now)

df = pd.read_csv(file_path.format(duration=duration, now=now), encoding='utf-8')
print(df.head())

print(df['rebal_qty'].sum(axis=0))
