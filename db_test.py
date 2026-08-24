import sqlite3

conn = sqlite3.connect("data/bike_system.db")
cursor = conn.cursor()

# 테이블 목록
cursor.execute("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
    ORDER BY name;
""")

tables = [row[0] for row in cursor.fetchall()]

for table in tables:
    print(f"\n===== {table} =====")

    cursor.execute(f"PRAGMA table_info({table})")

    for column in cursor.fetchall():
        print(column)

conn.close()