"""
db_backup.py — Render 재배포 대비 DB 백업/복원 (GitHub Gist)

전략: 사용자 입력 + 재생성 불가 데이터만 추출해서 Gist 보관.
재생성 가능한 데이터는 재부팅 후 자동 채워짐.

▶ 백업 대상 — DB 테이블:
  - trade_journal           (사용자 매매 기록 — 재생성 불가)
  - recommendation_history  (과거 추천 스냅샷 — 시점 데이터)
  - disclosure_history      (공시 + 매겨진 점수 — 점수 재계산 비용)
  - alert_rules             (사용자 알림 규칙)

▶ 백업 대상 — JSON 파일 (cache/):
  - server_watchlist.json   (관심종목)
  - server_portfolio.json   (포트폴리오)
  - alert_rules.json        (프론트 동기화용)

▶ 백업 제외 (재생성 가능):
  - stocks, ohlcv, chart_cache, flow_cache, financial, yinfo_cache (전부 재수집)
  - dart_corp_map (DART API로 매일 갱신)
  - alert_history (쿨다운 임시 데이터, 손실 무방)
"""
from __future__ import annotations
import os
import json
import gzip
import base64
import sqlite3
import logging
import urllib.request
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "dashboard.db"
GIST_FILE_NAME = "dashboard_core.db.gz.b64"

CORE_TABLES = [
    "trade_journal",            # 매매 기록 (수동 입력)
    "recommendation_history",   # 추천 스냅샷
    "disclosure_history",       # 공시 + 점수
    "alert_rules",              # 알림 규칙 (DB)
]

# 사용자 설정/입력이 들어있는 JSON 캐시 파일도 함께 백업
CORE_JSON_FILES = [
    "cache/server_watchlist.json",
    "cache/server_portfolio.json",
    "cache/alert_rules.json",   # 프론트 동기화 사본
]
CACHE_BUNDLE_FILE = "dashboard_cache.json"


def _gh_headers() -> dict | None:
    tok = os.getenv("GITHUB_TOKEN")
    if not tok:
        return None
    return {
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "stock-dashboard-backup",
    }


def _gh_request(url: str, method: str = "GET", payload: dict | None = None,
                timeout: int = 60) -> dict | None:
    h = _gh_headers()
    if not h:
        return None
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        log.warning("[DB백업] HTTP %d %s: %s", e.code, url, e.read()[:200])
    except Exception as exc:
        log.warning("[DB백업] req fail %s: %s", url, exc)
    return None


def _build_mini_db(src_path: Path) -> bytes | None:
    """src DB에서 CORE_TABLES만 새 메모리 DB로 추출 → bytes."""
    if not src_path.exists():
        return None
    src = sqlite3.connect(src_path)
    tmp_path = BASE_DIR / "db" / "_dashboard_core.tmp.db"
    if tmp_path.exists():
        try: tmp_path.unlink()
        except Exception: pass
    dst = sqlite3.connect(tmp_path)
    try:
        for tbl in CORE_TABLES:
            row = src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,)
            ).fetchone()
            if not row:
                log.debug("[DB백업] %s 테이블 없음 — skip", tbl)
                continue
            ddl = row[0]
            dst.execute(ddl)
            rows = src.execute(f"SELECT * FROM {tbl}").fetchall()
            if not rows:
                continue
            cols = [d[0] for d in src.execute(f"SELECT * FROM {tbl} LIMIT 0").description]
            qmarks = ",".join(["?"] * len(cols))
            dst.executemany(
                f"INSERT INTO {tbl} ({','.join(cols)}) VALUES ({qmarks})",
                rows
            )
        dst.commit()
    finally:
        src.close()
        dst.close()
    data = tmp_path.read_bytes()
    try: tmp_path.unlink()
    except Exception: pass
    return data


def _table_summary(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    try:
        c = sqlite3.connect(path)
        for t in CORE_TABLES:
            try:
                r = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
                out[t] = r[0] if r else 0
            except Exception:
                pass
        c.close()
    except Exception:
        pass
    return out


def backup_db() -> dict:
    """미니 DB 추출 → gzip + base64 → Gist 업로드/업데이트.
    return: {"ok": bool, "size_kb": int, "gist_id": str, ...}"""
    if not _gh_headers():
        return {"ok": False, "reason": "GITHUB_TOKEN 미설정"}
    if not DB_PATH.exists():
        return {"ok": False, "reason": "DB 파일 없음"}

    raw = _build_mini_db(DB_PATH)
    if not raw:
        return {"ok": False, "reason": "미니 DB 빌드 실패"}

    compressed = gzip.compress(raw)
    encoded = base64.b64encode(compressed).decode("ascii")
    size_kb = len(encoded) // 1024
    if size_kb > 25 * 1024:
        return {"ok": False, "reason": f"크기 초과 ({size_kb}KB)"}

    summary = _table_summary(DB_PATH)

    # JSON 파일 번들 (관심종목·포트폴리오·알림설정)
    cache_bundle = {}
    for rel in CORE_JSON_FILES:
        p = BASE_DIR / rel
        if p.exists():
            try:
                cache_bundle[rel] = p.read_text(encoding="utf-8")
            except Exception as exc:
                log.debug("[DB백업] %s 읽기 실패: %s", rel, exc)

    payload = {
        "description": f"stock-dashboard core backup {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "files": {
            GIST_FILE_NAME: {"content": encoded},
            CACHE_BUNDLE_FILE: {"content": json.dumps(cache_bundle, ensure_ascii=False, indent=2)},
            "backup_info.json": {"content": json.dumps({
                "timestamp": datetime.now().isoformat(),
                "raw_bytes": len(raw),
                "compressed_bytes": len(compressed),
                "tables": summary,
                "json_files": list(cache_bundle.keys()),
            }, indent=2, ensure_ascii=False)},
        },
    }

    gist_id = (os.getenv("BACKUP_GIST_ID") or "").strip()
    if gist_id:
        res = _gh_request(f"https://api.github.com/gists/{gist_id}",
                          "PATCH", payload)
    else:
        # 새 비공개 Gist 생성
        payload["public"] = False
        res = _gh_request("https://api.github.com/gists", "POST", payload)

    if not res or not res.get("id"):
        return {"ok": False, "reason": "Gist API 실패"}

    new_id = res["id"]
    log.info("[DB백업] 성공 — gist=%s, %dKB, tables=%s, json=%d",
             new_id, size_kb, summary, len(cache_bundle))
    return {"ok": True, "gist_id": new_id, "size_kb": size_kb,
            "raw_kb": len(raw) // 1024, "tables": summary,
            "json_files": list(cache_bundle.keys())}


def _find_backup_gist() -> str | None:
    res = _gh_request("https://api.github.com/gists?per_page=30")
    if not isinstance(res, list):
        return None
    for g in res:
        if GIST_FILE_NAME in (g.get("files") or {}):
            return g.get("id")
    return None


def _merge_into_main_db(mini_path: Path) -> dict:
    """mini DB의 행을 main DB에 INSERT OR REPLACE로 병합."""
    DB_PATH.parent.mkdir(exist_ok=True)
    main = sqlite3.connect(DB_PATH)
    mini = sqlite3.connect(mini_path)
    out = {}
    try:
        for tbl in CORE_TABLES:
            row = mini.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,)
            ).fetchone()
            if not row:
                continue
            # main에 테이블 없으면 mini DDL로 생성
            main_tbl = main.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,)
            ).fetchone()
            if not main_tbl:
                ddl = mini.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (tbl,)
                ).fetchone()[0]
                main.execute(ddl)
            cols = [d[0] for d in mini.execute(f"SELECT * FROM {tbl} LIMIT 0").description]
            rows = mini.execute(f"SELECT * FROM {tbl}").fetchall()
            if not rows:
                out[tbl] = 0
                continue
            qmarks = ",".join(["?"] * len(cols))
            main.executemany(
                f"INSERT OR REPLACE INTO {tbl} ({','.join(cols)}) VALUES ({qmarks})",
                rows
            )
            out[tbl] = len(rows)
        main.commit()
    finally:
        main.close()
        mini.close()
    return out


def restore_db() -> dict:
    """Gist에서 미니 DB 다운로드 → main DB로 병합. 서버 부팅 시 호출."""
    if not _gh_headers():
        return {"ok": False, "reason": "GITHUB_TOKEN 미설정"}

    gist_id = (os.getenv("BACKUP_GIST_ID") or "").strip()
    if not gist_id:
        gist_id = _find_backup_gist()
    if not gist_id:
        return {"ok": False, "reason": "백업 Gist 없음"}

    res = _gh_request(f"https://api.github.com/gists/{gist_id}")
    if not res:
        return {"ok": False, "reason": "Gist 조회 실패"}

    file_node = (res.get("files") or {}).get(GIST_FILE_NAME) or {}
    content = file_node.get("content")
    truncated = file_node.get("truncated")
    raw_url = file_node.get("raw_url")

    # truncated면 raw_url로 재요청 (Gist는 1MB 넘으면 truncate)
    if truncated and raw_url:
        try:
            req = urllib.request.Request(raw_url,
                headers={"User-Agent": "stock-dashboard-backup"})
            with urllib.request.urlopen(req, timeout=60) as r:
                content = r.read().decode("utf-8")
        except Exception as exc:
            return {"ok": False, "reason": f"raw_url fail: {exc}"}

    if not content:
        return {"ok": False, "reason": "Gist에 내용 없음"}

    try:
        compressed = base64.b64decode(content)
        raw = gzip.decompress(compressed)
    except Exception as exc:
        return {"ok": False, "reason": f"decode fail: {exc}"}

    DB_PATH.parent.mkdir(exist_ok=True)
    mini_path = BASE_DIR / "db" / "_restored_core.tmp.db"
    mini_path.write_bytes(raw)
    try:
        merged = _merge_into_main_db(mini_path)
    finally:
        try: mini_path.unlink()
        except Exception: pass

    # JSON 캐시 파일 복원 (관심종목/포트폴리오/알림설정)
    restored_json: list = []
    cache_node = (res.get("files") or {}).get(CACHE_BUNDLE_FILE) or {}
    cache_content = cache_node.get("content") or ""
    if cache_node.get("truncated") and cache_node.get("raw_url"):
        try:
            req2 = urllib.request.Request(cache_node["raw_url"],
                headers={"User-Agent": "stock-dashboard-backup"})
            with urllib.request.urlopen(req2, timeout=30) as r:
                cache_content = r.read().decode("utf-8")
        except Exception:
            pass
    if cache_content:
        try:
            cache_data = json.loads(cache_content)
            for rel, body in (cache_data or {}).items():
                target = BASE_DIR / rel
                # 이미 같은 내용이면 skip
                if target.exists():
                    try:
                        if target.read_text(encoding="utf-8") == body:
                            continue
                    except Exception:
                        pass
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
                restored_json.append(rel)
        except Exception as exc:
            log.debug("[DB복원] cache bundle 파싱 실패: %s", exc)

    log.info("[DB복원] DB 병합=%s, JSON 복원=%s", merged, restored_json)
    return {"ok": True, "gist_id": gist_id, "merged": merged,
            "json_restored": restored_json, "raw_kb": len(raw) // 1024}
