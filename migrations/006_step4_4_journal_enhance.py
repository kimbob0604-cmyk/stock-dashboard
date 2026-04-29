"""
Step 4-4-A: analysis_journal 컬럼 보강

4-1 스키마(39 컬럼) 그대로 유지 + 4개만 추가:
- analyst (분석가)
- priority (★ 표시)
- step3_peers_text (Step 3 정성 메모)
- thesis (핵심 논리 1줄)

기존 데이터 보존 (현재 0행).
인덱스는 4-1 그대로 유지.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db' / 'dashboard.db'


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(analysis_journal)")
    existing_cols = {row[1] for row in cur.fetchall()}

    print(f"기존 컬럼 수: {len(existing_cols)}")

    new_cols = [
        ('analyst', 'TEXT'),
        ('priority', 'TEXT'),
        ('step3_peers_text', 'TEXT'),
        ('thesis', 'TEXT'),
    ]

    added = []
    skipped = []

    for col_name, col_type in new_cols:
        if col_name in existing_cols:
            skipped.append(col_name)
        else:
            cur.execute(f"ALTER TABLE analysis_journal ADD COLUMN {col_name} {col_type}")
            added.append(col_name)

    conn.commit()

    print(f"\n=== 추가 결과 ===")
    print(f"  추가됨: {len(added)} ({added})")
    print(f"  이미 존재: {len(skipped)} ({skipped})")

    cur.execute("PRAGMA table_info(analysis_journal)")
    final_cols = [row[1] for row in cur.fetchall()]
    print(f"\n최종 컬럼 수: {len(final_cols)}")
    print(f"전체: {final_cols}")

    cur.execute("SELECT COUNT(*) FROM analysis_journal")
    print(f"\n기존 데이터: {cur.fetchone()[0]}건 (보존됨)")

    conn.close()


if __name__ == '__main__':
    migrate()
    print("\n✅ Step 4-4-A 마이그레이션 완료")
