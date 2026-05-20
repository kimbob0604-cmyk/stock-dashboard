"""
007: 'KUVIC' → '검토' 명칭 변경

코드 전반의 kuvic/KUVIC 식별자·라벨을 review/검토 로 교체한 것에 맞춰
DB에 저장된 분석가 값도 일괄 변경한다.

대상: analysis_journal.analyst = 'KUVIC' → '검토'
(그 외 테이블/JSON 컬럼에는 kuvic 흔적 없음 — 2026-05-20 확인)

멱등(idempotent): 이미 '검토'로 바뀐 행은 영향 없음.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db' / 'dashboard.db'


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    before = cur.execute(
        "SELECT COUNT(*) FROM analysis_journal WHERE analyst = 'KUVIC'"
    ).fetchone()[0]
    print(f"analyst='KUVIC' 대상 행: {before}")

    cur.execute(
        "UPDATE analysis_journal SET analyst = '검토' WHERE analyst = 'KUVIC'"
    )
    conn.commit()

    after = cur.execute(
        "SELECT COUNT(*) FROM analysis_journal WHERE analyst = 'KUVIC'"
    ).fetchone()[0]
    review_cnt = cur.execute(
        "SELECT COUNT(*) FROM analysis_journal WHERE analyst = '검토'"
    ).fetchone()[0]
    print(f"변경 후 analyst='KUVIC' 잔여: {after}, analyst='검토': {review_cnt}")

    conn.close()
    print("완료")


if __name__ == '__main__':
    migrate()
