"""EDINET API v2 大量保有報告書(5%ルール)自動取得エンジン

金融庁のEDINET APIを利用して、大量保有報告書・変更報告書のメタデータを取得し、
保有割合の増減や提出者(機関名)を抽出してDBに保存する。SPECIFICATION.md 2.4節に対応。

APIキーは引数、なければ環境変数 EDINET_API_KEY から取得する(旧アプリのconfig.ini設定に相当)。
"""
import os
import re
import time
from datetime import datetime, timedelta

import requests

from .nikkei_database import NikkeiDatabase

EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) kabutan_search/1.0"
REQUEST_TIMEOUT = 20
LARGE_HOLDING_DOC_TYPE = "350"  # 大量保有報告書・変更報告書


def get_api_key(explicit: str | None = None) -> str | None:
    """APIキーを取得する(引数 > 環境変数 EDINET_API_KEY の優先順)"""
    return explicit or os.environ.get("EDINET_API_KEY")


def has_api_key(api_key: str | None) -> bool:
    return bool(api_key and api_key.strip())


def should_sync(nikkei_db: NikkeiDatabase, api_key: str | None) -> bool:
    """同期が必要か判定する。APIキー未設定・土日・本日既に同期済みならFalse"""
    if not has_api_key(api_key):
        return False
    if datetime.now().weekday() >= 5:
        return False
    last_date = nikkei_db.get_latest_edinet_date()
    return last_date != datetime.now().strftime("%Y-%m-%d")


def _extract_holding_info(doc: dict) -> dict | None:
    """EDINET APIレスポンスの1件のドキュメントから大量保有情報を抽出する"""
    doc_id = doc.get("docID", "")
    if not doc_id:
        return None

    filer_name = doc.get("filerName", "")
    if not filer_name:
        return None

    sec_code = doc.get("secCode", "")
    if sec_code:
        sec_code = str(sec_code).strip()
        if len(sec_code) >= 4:
            sec_code = sec_code[:4]
        if not re.match(r"^\d{4}$", sec_code):
            sec_code = None

    doc_title = doc.get("docDescription", "")
    report_type = "変更報告書" if "変更" in doc_title else "大量保有報告書"

    holding_ratio = None
    if doc_title:
        ratio_matches = re.findall(r"(\d+\.?\d*)\s*[%％]", doc_title)
        if ratio_matches:
            holding_ratio = float(ratio_matches[0])

    issuer_name = doc.get("edinetCode", "") or doc.get("subjectEdinetCode", "")

    filing_date = doc.get("submitDateTime", "")
    if filing_date:
        filing_date = filing_date[:10]  # "2026-01-15 09:00:00" -> "2026-01-15"

    return {
        "doc_id": doc_id,
        "date": filing_date,
        "submission_date": filing_date,
        "code": sec_code,
        "issuer_name": issuer_name,
        "holder_name": filer_name,
        "holding_ratio": holding_ratio,
        "report_type": report_type,
        "purpose": doc_title,
    }


def fetch_documents_for_date(date_str: str, api_key: str, session: requests.Session | None = None) -> list[dict]:
    """指定日の提出書類一覧を取得し、大量保有報告書(docTypeCode=350)のみ抽出する"""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)

    params = {"date": date_str, "type": 2, "Subscription-Key": api_key}
    try:
        resp = session.get(f"{EDINET_API_BASE}/documents.json", params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    if data.get("metadata", {}).get("status") != "200":
        return []

    records = []
    for doc in data.get("results", []):
        if str(doc.get("docTypeCode", "")) == LARGE_HOLDING_DOC_TYPE:
            rec = _extract_holding_info(doc)
            if rec:
                records.append(rec)
    return records


def sync(nikkei_db: NikkeiDatabase, api_key: str | None = None, days_back: int | None = None) -> dict:
    """過去days_back日分の大量保有報告書を取得しDBへ保存する。

    days_back未指定時: 初回同期(DBが空)なら30日分、以降は7日分。
    """
    api_key = get_api_key(api_key)
    result = {"success": False, "records_count": 0, "messages": []}

    if not has_api_key(api_key):
        result["messages"].append("EDINET APIキーが設定されていません(環境変数 EDINET_API_KEY)。")
        return result

    if days_back is None:
        days_back = 30 if nikkei_db.get_latest_edinet_date() is None else 7

    session = requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)

    all_records = []
    today = datetime.now().date()
    for i in range(days_back):
        target_date = today - timedelta(days=i)
        if target_date.weekday() >= 5:  # 土日はスキップ
            continue
        all_records.extend(fetch_documents_for_date(target_date.strftime("%Y-%m-%d"), api_key, session))
        time.sleep(0.5)  # APIへの配慮(polite delay)

    for rec in all_records:
        nikkei_db.upsert_edinet_large_holding(rec)

    result["success"] = True
    result["records_count"] = len(all_records)
    result["messages"].append(
        f"EDINET大量保有報告書: {len(all_records)}件を取得・保存しました" if all_records
        else "EDINET: 該当する大量保有報告書は見つかりませんでした"
    )
    return result
