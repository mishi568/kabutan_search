"""JPX公式ファイル(PDF/Excel/CSV)の解析・DB取り込み

jpx_auto_syncer.py がダウンロードしたファイル(または手動ダウンロードしたファイル)を解析し、
以下へ保存する:
  - 33業種別空売り比率  → kabutan_stock.db の sector_daily_records.short_ratio
    (sector_data_manager.py が既に作成済みの当日レコードへ upsert する。まだ無い日付には反映されない)
  - 全体の空売り比率    → nikkei_data.db の jpx_short_selling
  - 投資部門別売買動向  → nikkei_data.db の jpx_investor_trends
  - 個別銘柄信用取引残高 → nikkei_data.db の jpx_margin_positions
"""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from .database import Database
from .nikkei_database import NikkeiDatabase

SECTOR_33_NAMES = [
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品",
    "パルプ・紙", "化学", "医薬品", "石油・石炭製品", "ゴム製品",
    "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", "機械",
    "電気機器", "輸送用機器", "精密機器", "その他製品", "電気・ガス業",
    "陸運業", "海運業", "空運業", "倉庫・運輸関連業", "情報・通信業",
    "卸売業", "小売業", "銀行業", "証券、商品先物取引業", "保険業",
    "その他金融業", "不動産業", "サービス業", "その他(33業種外)",
]

_CSV_ENCODINGS = ["utf-8-sig", "shift_jis", "cp932", "euc-jp", "utf-8"]


def import_file(db: Database, nikkei_db: NikkeiDatabase, filepath: str) -> dict:
    """ファイル形式を検出し、解析してDBへ保存する"""
    path = Path(filepath)
    if not path.exists():
        return {"success": False, "error": f"ファイルが見つかりません: {filepath}"}

    ext = path.suffix.lower()
    if ext not in (".csv", ".xlsx", ".xls", ".pdf"):
        return {"success": False, "error": f"未対応のファイル形式です ({ext})。PDF、CSV、またはExcelファイルを指定してください。"}

    try:
        if ext == ".pdf":
            return _parse_and_save_pdf(db, nikkei_db, path)

        file_type = detect_file_type(path)
        if file_type == "INVESTOR_TRENDS":
            return _parse_and_save_investor_trends(nikkei_db, path)
        if file_type == "SHORT_SELLING":
            return _parse_and_save_short_selling(db, nikkei_db, path)
        if file_type == "MARGIN_POSITIONS":
            return _parse_and_save_margin_positions(db, nikkei_db, path)
        return {"success": False, "error": "JPXデータ(空売り集計、投資部門別売買状況、信用取引残高)の形式を自動判別できませんでした。"}
    except Exception as e:
        return {"success": False, "error": f"インポート処理中にエラーが発生しました: {e}"}


def detect_file_type(path: Path) -> str:
    """ファイル名とファイル内容からJPXデータの種類を判定する"""
    filename = path.name.lower()
    ext = path.suffix.lower()

    if any(k in filename for k in ["short", "karauri", "空売り", "-g.", "-m."]):
        return "SHORT_SELLING"
    if any(k in filename for k in ["stock_vol", "stock_val", "investor", "主体別", "部門別", "trends"]):
        return "INVESTOR_TRENDS"
    if any(k in filename for k in ["mtdaily", "margin", "shinyo", "信用残", "週末残高"]):
        return "MARGIN_POSITIONS"

    content_sample = ""
    if ext == ".csv":
        for enc in _CSV_ENCODINGS:
            try:
                with open(path, "r", encoding=enc, errors="ignore") as f:
                    content_sample = "".join(f.readline() for _ in range(25))
                break
            except (UnicodeDecodeError, OSError):
                continue
    elif ext in (".xlsx", ".xls"):
        try:
            df_sample = pd.read_excel(path, header=None, nrows=20)
            content_sample = df_sample.to_string()
        except (ValueError, OSError):
            pass

    if any(k in content_sample for k in ["海外", "外国人", "個人", "信託", "Brokerage", "Investor Type", "Foreigners", "Individuals"]):
        return "INVESTOR_TRENDS"
    if any(k in content_sample for k in ["空売り", "規制", "Short Selling", "実線"]):
        return "SHORT_SELLING"
    if any(k in content_sample for k in ["信用", "売残", "買残", "貸借", "Margin Trading", "mtdaily"]):
        return "MARGIN_POSITIONS"

    return "UNKNOWN"


def _extract_date_from_text(text: str, filename: str = "") -> str | None:
    m = re.search(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    m = re.search(r"20(\d{2})(\d{2})(\d{2})", filename)
    if m:
        return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = re.search(r"^(\d{2})(\d{2})(\d{2})", filename)
    if m:
        return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return None


def _normalize_date(date_str) -> str | None:
    """「YYYY/MM/DD」「YYYY年M月D日」等の表記を YYYY-MM-DD に変換する"""
    if not date_str:
        return None
    s = str(date_str).strip()
    m = re.search(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{2})/(\d{2})/(\d{2})", s)
    if m:
        return f"20{int(m.group(1)):02d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _to_float(val, default: float = 0.0) -> float:
    try:
        s = str(val).replace(",", "").replace("▲", "-").replace("▼", "-").replace("+", "").strip()
        return float(s) if s and s != "-" else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# PDF (空売り集計・個別銘柄信用残高)
# ---------------------------------------------------------------------------

def _parse_and_save_pdf(db: Database, nikkei_db: NikkeiDatabase, path: Path) -> dict:
    try:
        import pdfplumber
    except ImportError:
        return {"success": False, "error": "PDFの読み込みに必要なライブラリ(pdfplumber)がインストールされていません。"}

    filename = path.name.lower()

    with pdfplumber.open(path) as pdf:
        all_text = ""
        all_tables = []
        for page in pdf.pages:
            all_text += (page.extract_text() or "") + "\n"
            all_tables.extend(page.extract_tables() or [])

    date_str = _extract_date_from_text(all_text, filename) or datetime.now().strftime("%Y-%m-%d")

    # Priority 1: 個別銘柄信用取引残高 (mtdailyk*.pdf)
    if "mtdaily" in filename or "個別銘柄信用取引残高" in all_text or "Outstanding Margin Trading by Issue" in all_text or "JP3" in all_text:
        margin_records = []
        for line in all_text.splitlines():
            isin_match = re.search(r"([0-9A-Z]{4,5}0?)\s+(JP[0-9A-Z]{10})\s+(.*)", line)
            if not isin_match:
                continue
            raw_code, rest = isin_match.group(1).strip(), isin_match.group(3)
            code = raw_code[:4]

            cleaned_rest = rest.replace("▲ ", "-").replace("▲", "-").replace("▼ ", "-").replace("▼", "-")
            nums = []
            for t in cleaned_rest.split():
                t_clean = t.replace(",", "").replace("*", "").strip()
                if t_clean == "-":
                    nums.append(0.0)
                else:
                    try:
                        nums.append(float(t_clean))
                    except ValueError:
                        pass

            if len(nums) < 2:
                continue

            sell_shares = nums[0]
            sell_change = nums[1] if len(nums) > 1 else 0.0
            buy_shares = nums[3] if len(nums) > 3 else (nums[1] if len(nums) == 2 else 0.0)
            buy_change = nums[4] if len(nums) > 4 else 0.0

            if len(nums) > 6 and nums[6] >= 0.0:
                margin_ratio = nums[6]
            elif sell_shares > 0:
                margin_ratio = round(buy_shares / sell_shares, 2)
            else:
                margin_ratio = 0.0

            margin_records.append({
                "date": date_str, "code": code, "name": db.get_stock_name(code) or f"銘柄{code}",
                "margin_sell": sell_shares, "margin_buy": buy_shares, "margin_ratio": margin_ratio,
                "margin_sell_change": sell_change, "margin_buy_change": buy_change,
            })

        if margin_records:
            for rec in margin_records:
                nikkei_db.upsert_jpx_margin_position(rec)
            return {
                "success": True, "file_type": "MARGIN_POSITIONS", "date": date_str,
                "rows_saved": len(margin_records),
                "message": f"【個別銘柄信用取引残高】{date_str} の信用残データ({len(margin_records)}銘柄)を正常に取り込みました。",
            }

    # Priority 2: 空売り集計(全体 or 33業種)
    sector_rows, overall_rows = [], []
    for t in all_tables:
        for r in t:
            if not r or not any(r):
                continue
            pcts = [cell for cell in r if cell and "%" in str(cell)]
            if len(pcts) >= 3:
                sector_rows.append(r)
            elif len(pcts) >= 2 or (len(r) >= 5 and any("%" in str(c) for c in r if c)):
                overall_rows.append(r)

    if len(sector_rows) >= 25 or "-g" in filename or "業種" in all_text:
        sector_ratios = {}
        for i, r in enumerate(sector_rows):
            sec_name = SECTOR_33_NAMES[i] if i < len(SECTOR_33_NAMES) else f"業種{i + 1}"
            pcts = [cell for cell in r if cell and "%" in str(cell)]
            if len(pcts) >= 3:
                try:
                    p_reg = float(pcts[1].replace("%", "").replace(",", "").strip())
                    p_non_reg = float(pcts[2].replace("%", "").replace(",", "").strip())
                    sector_ratios[sec_name] = round(p_reg + p_non_reg, 2)
                except ValueError:
                    pass

        if sector_ratios:
            db.batch_update_sector_short_ratios(date_str, sector_ratios)
            return {
                "success": True, "file_type": "SHORT_SELLING_SECTORS", "date": date_str,
                "rows_saved": len(sector_ratios),
                "message": f"【空売り集計(33業種別)】{date_str} の33業種データを正常に取り込みました({len(sector_ratios)}業種)。",
            }

    if overall_rows or len(sector_rows) == 1 or "-m" in filename or "(a)/(d)" in all_text or "空売り" in all_text:
        target_r = sector_rows[0] if sector_rows else (overall_rows[0] if overall_rows else None)
        if target_r:
            pcts = [cell for cell in target_r if cell and "%" in str(cell)]
            if len(pcts) >= 3:
                try:
                    p_reg = float(pcts[1].replace("%", "").replace(",", "").strip())
                    p_non_reg = float(pcts[2].replace("%", "").replace(",", "").strip())
                    overall_ratio = round(p_reg + p_non_reg, 2)

                    total_val = None
                    nums = [cell for cell in target_r if cell and str(cell).replace(",", "").replace(".", "").strip().isdigit()]
                    if nums:
                        total_val = _to_float(nums[-1], default=None)

                    nikkei_db.upsert_jpx_short_selling({
                        "date": date_str, "short_selling_ratio": overall_ratio,
                        "regulated_ratio": p_reg, "non_regulated_ratio": p_non_reg,
                        "total_value": total_val,
                    })
                    return {
                        "success": True, "file_type": "SHORT_SELLING_OVERALL", "date": date_str, "rows_saved": 1,
                        "message": f"【空売り集計(全体比率)】{date_str} の全体空売り比率 {overall_ratio}% を正常に取り込みました。",
                    }
                except ValueError:
                    pass

    return {"success": False, "error": f"PDFファイル({filename})から有効なJPXデータ(空売り集計・信用残高・主体別)を抽出できませんでした。"}


# ---------------------------------------------------------------------------
# 投資部門別売買動向 (CSV/Excel)
# ---------------------------------------------------------------------------

def _parse_and_save_investor_trends(nikkei_db: NikkeiDatabase, path: Path) -> dict:
    ext = path.suffix.lower()
    records_saved = 0
    latest_date = None

    if ext == ".csv":
        df = None
        for enc in _CSV_ENCODINGS[:4]:
            try:
                df = pd.read_csv(path, encoding=enc, skiprows=2)
                break
            except UnicodeDecodeError:
                continue
        if df is None:
            return {"success": False, "error": "CSVファイルのエンコーディングを判定できませんでした。"}

        for _, row in df.iterrows():
            date_val = str(row.iloc[0]).strip()
            if not date_val or date_val.lower() == "nan":
                continue
            formatted_date = _normalize_date(date_val)
            if not formatted_date:
                continue

            rec = {
                "date": formatted_date,
                "foreign_net": _to_float(row.iloc[3]) if len(row) > 3 else 0.0,
                "individual_net": _to_float(row.iloc[5]) if len(row) > 5 else 0.0,
                "investment_trust_net": _to_float(row.iloc[8]) if len(row) > 8 else 0.0,
                "business_corp_net": _to_float(row.iloc[9]) if len(row) > 9 else 0.0,
                "trust_bank_net": _to_float(row.iloc[11]) if len(row) > 11 else 0.0,
            }
            nikkei_db.upsert_jpx_investor_trends(rec)
            records_saved += 1
            latest_date = formatted_date

    elif ext in (".xlsx", ".xls"):
        xl = pd.ExcelFile(path)
        for sheet_name in xl.sheet_names:
            df = xl.parse(sheet_name, header=None)

            header_date_str = None
            for r in range(min(15, len(df))):
                row_text = " ".join(str(c) for c in df.iloc[r] if pd.notna(c))
                d_match = re.search(r"\(?\s*(\d{1,2})[/月](\d{1,2})\s*[-〜~]\s*(\d{1,2})[/月](\d{1,2})\s*\)?", row_text)
                if d_match:
                    y = datetime.now().year
                    header_date_str = f"{y:04d}-{int(d_match.group(3)):02d}-{int(d_match.group(4)):02d}"
                    break
            if not header_date_str:
                fn_match = re.search(r"(\d{2})(\d{2})(\d{2})", path.name)
                header_date_str = (
                    f"20{fn_match.group(1)}-{fn_match.group(2)}-{fn_match.group(3)}"
                    if fn_match else datetime.now().strftime("%Y-%m-%d")
                )

            data_start_row = None
            for r in range(len(df)):
                row_vals = [str(c) for c in df.iloc[r] if pd.notna(c)]
                if any("海外投資家" in v or "外国人" in v or "Foreigners" in v or "個人" in v or "Individuals" in v for v in row_vals):
                    data_start_row = r
                    break
            if data_start_row is None:
                continue

            def _extract_diff(row_idx):
                if row_idx >= len(df):
                    return 0.0
                for c in reversed(list(df.iloc[row_idx])):
                    if pd.notna(c):
                        try:
                            return _to_float(c)
                        except (ValueError, TypeError):
                            continue
                return 0.0

            foreign_net = individual_net = trust_net = investment_trust_net = business_corp_net = other_net = 0.0
            for r in range(data_start_row, min(data_start_row + 40, len(df))):
                row_str = " ".join(str(c) for c in df.iloc[r] if pd.notna(c))
                if any(k in row_str for k in ["海外投資家", "外国人", "Foreigners"]):
                    foreign_net = _extract_diff(r)
                elif any(k in row_str for k in ["個人", "Individuals"]):
                    individual_net = _extract_diff(r)
                elif any(k in row_str for k in ["信託銀行", "Trust Banks"]):
                    trust_net = _extract_diff(r)
                elif any(k in row_str for k in ["投資信託", "Investment Trusts"]):
                    investment_trust_net = _extract_diff(r)
                elif any(k in row_str for k in ["事業法人", "Corporations", "Business Corps"]):
                    business_corp_net = _extract_diff(r)
                elif any(k in row_str for k in ["自己計", "Proprietary Total", "Proprietary"]):
                    other_net = _extract_diff(r)

            nikkei_db.upsert_jpx_investor_trends({
                "date": header_date_str, "foreign_net": foreign_net, "individual_net": individual_net,
                "trust_bank_net": trust_net, "investment_trust_net": investment_trust_net,
                "business_corp_net": business_corp_net, "other_net": other_net,
            })
            records_saved += 1
            latest_date = header_date_str

    if records_saved > 0:
        return {
            "success": True, "file_type": "INVESTOR_TRENDS", "date": latest_date, "rows_saved": records_saved,
            "message": f"【投資部門別売買状況】{latest_date}(計{records_saved}件)のデータを正常に取り込みました。",
        }
    return {"success": False, "error": "投資部門別売買状況の有効なデータ行を抽出できませんでした。"}


# ---------------------------------------------------------------------------
# 空売り集計 (CSV/Excel)
# ---------------------------------------------------------------------------

def _parse_and_save_short_selling(db: Database, nikkei_db: NikkeiDatabase, path: Path) -> dict:
    ext = path.suffix.lower()
    date_str = None
    overall_ratio = None
    sectors_dict: dict[str, float] = {}

    fn_match = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", path.name)
    if fn_match:
        date_str = f"{fn_match.group(1)}-{fn_match.group(2)}-{fn_match.group(3)}"
    else:
        fn_match2 = re.search(r"(\d{2})(\d{2})(\d{2})", path.name)
        if fn_match2:
            date_str = f"20{fn_match2.group(1)}-{fn_match2.group(2)}-{fn_match2.group(3)}"

    if ext == ".csv":
        df = None
        for enc in _CSV_ENCODINGS[:4]:
            try:
                df = pd.read_csv(path, encoding=enc, header=None)
                break
            except UnicodeDecodeError:
                continue
        if df is None:
            return {"success": False, "error": "CSVファイルのエンコーディングを判定できませんでした。"}
    elif ext in (".xlsx", ".xls"):
        xl = pd.ExcelFile(path)
        df = xl.parse(xl.sheet_names[0], header=None)
    else:
        return {"success": False, "error": "未対応の形式です。"}

    for r in range(min(15, len(df))):
        row_str = " ".join(str(c) for c in df.iloc[r] if pd.notna(c))
        d_m = re.search(r"(\d{4})[/年.-](\d{1,2})[/月.-](\d{1,2})", row_str)
        if d_m:
            date_str = f"{int(d_m.group(1)):04d}-{int(d_m.group(2)):02d}-{int(d_m.group(3)):02d}"
            break

    for r in range(len(df)):
        row_vals = [str(c).strip() for c in df.iloc[r] if pd.notna(c)]
        row_str = " ".join(row_vals)

        if "全体" in row_str or "合計" in row_str or "Total" in row_str:
            for v in row_vals:
                if "%" in v or (re.match(r"^\d+\.\d+$", v) and 20.0 <= float(v) <= 60.0):
                    val = float(v.replace("%", ""))
                    if overall_ratio is None:
                        overall_ratio = val

        for sec in SECTOR_33_NAMES:
            if sec in row_str:
                for v in reversed(row_vals):
                    try:
                        f_val = float(v.replace("%", "").replace(",", ""))
                        if 10.0 <= f_val <= 70.0:
                            sectors_dict[sec] = f_val
                            break
                    except ValueError:
                        continue

    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    if overall_ratio is None and sectors_dict:
        overall_ratio = round(sum(sectors_dict.values()) / len(sectors_dict), 2)

    if overall_ratio is None and not sectors_dict:
        return {"success": False, "error": "空売り集計の有効な比率データを抽出できませんでした。"}

    nikkei_db.upsert_jpx_short_selling({"date": date_str, "short_selling_ratio": overall_ratio or 40.0})
    if sectors_dict:
        db.batch_update_sector_short_ratios(date_str, sectors_dict)

    return {
        "success": True, "file_type": "SHORT_SELLING", "date": date_str,
        "rows_saved": len(sectors_dict) if sectors_dict else 1,
        "message": f"【空売り集計】{date_str} の空売り比率データ(全体比率: {overall_ratio or '--'}% / 業種別: {len(sectors_dict)}件)を正常に取り込みました。",
    }


# ---------------------------------------------------------------------------
# 個別銘柄信用取引残高 (CSV/Excel)
# ---------------------------------------------------------------------------

def _parse_and_save_margin_positions(db: Database, nikkei_db: NikkeiDatabase, path: Path) -> dict:
    ext = path.suffix.lower()
    date_str = None

    fn_match = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", path.name)
    if fn_match:
        date_str = f"{fn_match.group(1)}-{fn_match.group(2)}-{fn_match.group(3)}"
    else:
        fn_match2 = re.search(r"(\d{2})(\d{2})(\d{2})", path.name)
        if fn_match2:
            date_str = f"20{fn_match2.group(1)}-{fn_match2.group(2)}-{fn_match2.group(3)}"

    df = None
    if ext == ".csv":
        for enc in ["utf-8-sig", "cp932", "shift_jis", "utf-8", "euc-jp"]:
            try:
                df = pd.read_csv(path, encoding=enc, header=None)
                break
            except (UnicodeDecodeError, OSError):
                continue
    else:
        try:
            xl = pd.ExcelFile(path)
            df = xl.parse(xl.sheet_names[0], header=None)
        except (ValueError, OSError) as e:
            return {"success": False, "error": f"Excelファイルの読み込みに失敗しました: {e}"}

    if df is None:
        return {"success": False, "error": "信用取引残高ファイルを読み込めませんでした。"}

    if not date_str:
        for r in range(min(10, len(df))):
            row_str = " ".join(str(c) for c in df.iloc[r] if pd.notna(c))
            d_m = re.search(r"(\d{4})[/年.-](\d{1,2})[/月.-](\d{1,2})", row_str)
            if d_m:
                date_str = f"{int(d_m.group(1)):04d}-{int(d_m.group(2)):02d}-{int(d_m.group(3)):02d}"
                break
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

    margin_records = []
    for _, row in df.iterrows():
        row_vals = [str(v).strip() for v in row if pd.notna(v)]
        if not row_vals or len(row_vals) < 3:
            continue

        code = None
        name = ""

        for i, v in enumerate(row_vals):
            if re.match(r"^\d{4}0?$", v) and len(v) in (4, 5):
                code = v[:4]
                for prev_v in row_vals[:i]:
                    if (
                        len(prev_v) > 1
                        and not re.match(r"^[\d,\.\-\+▲▼%]+$", prev_v)
                        and prev_v not in ["B", "貸", "融", "スタンダード", "プライム", "グロース", "株", "Section", "Code", "Loan/\nMargin"]
                    ):
                        name = prev_v.replace("　", " ").replace(" 普通", "").replace("　普通", "").strip()
                break

        if not code:
            for v in row_vals[:3]:
                if re.match(r"^\d{4}$", v):
                    code = v
                    break

        if not code:
            continue

        if not name:
            for v in row_vals:
                if len(v) > 1 and not re.match(r"^[\d,\.\-\+▲▼%]+$", v) and v != code and "JP" not in v:
                    name = v.replace("　", " ").replace(" 普通", "").strip()
                    break
        if not name:
            name = db.get_stock_name(code) or f"銘柄{code}"

        sell_shares = sell_change = buy_shares = buy_change = 0.0
        if len(row) >= 14 and pd.notna(row.iloc[8]) and pd.notna(row.iloc[11]):
            sell_shares = _to_float(row.iloc[8])
            sell_change = _to_float(row.iloc[9])
            buy_shares = _to_float(row.iloc[11])
            buy_change = _to_float(row.iloc[12])
        else:
            nums = []
            for v in row_vals:
                if v == code or (len(v) == 5 and v[:4] == code):
                    continue
                try:
                    nums.append(float(v.replace(",", "").replace("▲", "-").replace("▼", "-").replace("+", "").strip()))
                except ValueError:
                    pass
            if len(nums) >= 2:
                sell_shares, buy_shares = nums[0], nums[1]
                sell_change = nums[2] if len(nums) > 2 else 0.0
                buy_change = nums[3] if len(nums) > 3 else 0.0

        ratio = round(buy_shares / sell_shares, 2) if sell_shares > 0 else 0.0

        margin_records.append({
            "date": date_str, "code": code, "name": name,
            "margin_sell": sell_shares, "margin_buy": buy_shares, "margin_ratio": ratio,
            "margin_sell_change": sell_change, "margin_buy_change": buy_change,
        })

    if not margin_records:
        return {"success": False, "error": "信用取引残高の有効な銘柄データ行を抽出できませんでした。"}

    for rec in margin_records:
        nikkei_db.upsert_jpx_margin_position(rec)

    return {
        "success": True, "file_type": "MARGIN_POSITIONS", "date": date_str, "rows_saved": len(margin_records),
        "message": f"【個別銘柄信用取引残高】{date_str} の信用残高データ(計{len(margin_records)}銘柄)を正常に取り込みました。",
    }
