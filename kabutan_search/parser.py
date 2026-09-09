"""株探(kabutan.jp)のHTML解析

BeautifulSoupと正規表現でページから株価・財務データを抽出する。
返却する辞書のキーは database.STOCK_RECORD_COLUMNS のフィールド名に対応する。
"""
import re

from bs4 import BeautifulSoup

_RATE_LIMIT_MARKERS = (
    "ご覧になろうとしているページは現在表示できません",
    "一時的なエラーですので",
)


def is_rate_limited(html: str) -> bool:
    """株探のアクセス制限ページかどうかを判定する"""
    return any(marker in html for marker in _RATE_LIMIT_MARKERS)


def clean_numeric(val):
    """数値文字列から最初の整数/浮動小数点数を抽出する"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    val = str(val).strip()
    if not val or val in ["---", "--", "－", "―", "—", "|", "－*"]:
        return None
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", val.replace(",", ""))
    if m:
        num_str = m.group(1)
        if "." in num_str:
            return float(num_str)
        return int(num_str)
    return None


def parse_money_japanese(text):
    """「48兆4,274億円」のような日本語表記の金額を百万円単位に変換する"""
    if not text or text in ["---", "--", "－"]:
        return None
    text = text.replace(",", "").strip()
    trillion = 0
    hundred_million = 0

    m_trillion = re.search(r"(\d+)\s*兆", text)
    if m_trillion:
        trillion = int(m_trillion.group(1))

    m_hundred_million = re.search(r"(?:兆|^)\s*(\d+)\s*億", text)
    if m_hundred_million:
        hundred_million = int(m_hundred_million.group(1))
    else:
        m_hundred_million_alt = re.search(r"(\d+)\s*億円", text)
        if m_hundred_million_alt:
            hundred_million = int(m_hundred_million_alt.group(1))

    return trillion * 1000000 + hundred_million * 100


def parse_top_page(html: str) -> dict | None:
    """株探の個別銘柄トップページを解析する。

    'date' は取得時刻が分からないためここでは含めない(呼び出し側で付与する)。
    アクセス制限ページの場合は {"error": "rate_limit_blocked"} を返す。
    有効な銘柄コードが見つからない場合は None を返す。
    """
    if not html:
        return None
    if is_rate_limited(html):
        return {"error": "rate_limit_blocked"}

    soup = BeautifulSoup(html, "html.parser")

    code = None
    name = None

    # 1. コード・銘柄名を si_i1_1 内のh2から取得
    si_i1_1 = soup.find("div", class_="si_i1_1")
    if si_i1_1:
        h2 = si_i1_1.find("h2")
        if h2:
            code_span = h2.find(class_="inline-block")
            if code_span:
                code = code_span.get_text(strip=True)
            name = h2.contents[-1].strip() if h2.contents else None

    # 2. フォールバック: h1タグ
    if not code:
        h1 = soup.find("h1")
        if h1:
            h1_text = h1.get_text(strip=True)
            m = re.search(r"([^\(]+)\((\d{4})\)", h1_text)
            if m:
                name = m.group(1).strip()
                code = m.group(2).strip()

    # 3. 最終フォールバック: titleタグ
    if not code:
        title = soup.find("title")
        if title:
            title_text = title.get_text(strip=True)
            m = re.search(r"^(.+)【(\d{4})】", title_text)
            if m:
                name = m.group(1).strip()
                code = m.group(2).strip()

    if not code:
        return None

    data = {
        "code": code,
        "name": name if name else "Unknown",
        "price": None,
        "price_change": 0.0,
        "price_change_percent": 0.0,
    }

    stat_keys = [
        "previous_close", "open", "high", "low", "volume", "trading_value",
        "market_cap", "issued_shares", "dividend_yield", "dps", "per", "per_avg_3",
        "pbr", "eps", "bps", "roe", "equity_ratio", "min_purchase_price", "round_lot",
        "year_high", "year_high_date", "year_low", "year_low_date",
        "margin_buy", "margin_buy_change", "margin_ratio", "margin_sell", "margin_sell_change",
        "earnings_date", "has_yutai", "sector", "settlement_month",
    ]
    for sk in stat_keys:
        data[sk] = None

    # 4. 現在値
    kabuka_span = soup.find("span", class_="kabuka")
    if kabuka_span:
        data["price"] = clean_numeric(kabuka_span.get_text(strip=True))

    # 5. 前日比・前日比(%)
    dl_change = soup.find("dl", class_="si_i1_dl1")
    if dl_change:
        dds = dl_change.find_all("dd")
        if len(dds) >= 1:
            data["price_change"] = clean_numeric(dds[0].get_text(strip=True))
        if len(dds) >= 2:
            data["price_change_percent"] = clean_numeric(dds[1].get_text(strip=True))

    # 6. PER・PBR・利回り・信用倍率・時価総額
    si_i3 = soup.find("div", id="stockinfo_i3")
    if si_i3:
        table = si_i3.find("table")
        if table:
            tbody = table.find("tbody")
            if tbody:
                trs = tbody.find_all("tr")
                if len(trs) >= 1:
                    tds = trs[0].find_all("td")
                    if len(tds) >= 4:
                        data["per"] = clean_numeric(tds[0].get_text(strip=True))
                        data["pbr"] = clean_numeric(tds[1].get_text(strip=True))
                        data["dividend_yield"] = clean_numeric(tds[2].get_text(strip=True))
                        data["margin_ratio"] = clean_numeric(tds[3].get_text(strip=True))
                if len(trs) >= 2:
                    td_zika = trs[1].find("td", class_="v_zika2")
                    if td_zika:
                        data["market_cap"] = parse_money_japanese(td_zika.get_text(strip=True))

    # 7. 全テーブルを走査してラベル一致するものを拾う(出来高・売買代金等)
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) >= 2:
                key, val = cells[0], cells[1]
                if "出来高" in key and "出来高(5日" not in key:
                    if data.get("volume") is None:
                        data["volume"] = clean_numeric(val)
                elif "売買代金" in key:
                    val_millions = clean_numeric(val)
                    if val_millions is not None and data.get("trading_value") is None:
                        data["trading_value"] = val_millions * 1000
                elif "売買最低代金" in key:
                    if data.get("min_purchase_price") is None:
                        data["min_purchase_price"] = clean_numeric(val)
                elif "単元株数" in key:
                    if data.get("round_lot") is None:
                        data["round_lot"] = clean_numeric(val)
                elif "発行済株式数" in key:
                    data["issued_shares"] = clean_numeric(val)

    # 8. 前日終値
    for dl in soup.find_all("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if dt and dd and "前日終値" in dt.get_text(strip=True):
            data["previous_close"] = clean_numeric(dd.get_text(strip=True))

    # 9. 始値・高値・安値
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if "始値" in headers and "現在値" in headers:
            for tr in table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) >= 2:
                    k, v = cells[0], cells[1]
                    if "始値" in k:
                        data["open"] = clean_numeric(v)
                    elif "高値" in k:
                        data["high"] = clean_numeric(v)
                    elif "安値" in k:
                        data["low"] = clean_numeric(v)

    # 10. EPS・DPS予想 & 決算発表日フォールバック
    fallback_earnings_date = None
    for table in soup.find_all("table"):
        first_tr = table.find("tr")
        if not first_tr:
            continue
        headers = [th.get_text(strip=True) for th in first_tr.find_all(["th", "td"])]
        headers_cleaned = [h.replace("１", "1").replace("　", "").strip() for h in headers]
        if "決算期" in headers_cleaned and ("1株益" in headers_cleaned or "1株配" in headers_cleaned):
            col_eps = headers_cleaned.index("1株益") if "1株益" in headers_cleaned else -1
            col_dps = headers_cleaned.index("1株配") if "1株配" in headers_cleaned else -1
            col_pub = -1
            for pub_header in ["発表日", "決算発表日", "開示日"]:
                if pub_header in headers_cleaned:
                    col_pub = headers_cleaned.index(pub_header)
                    break

            trs = table.find_all("tr")
            for tr in trs:
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) > max(col_eps, col_dps) and "予" in cells[0]:
                    if col_eps != -1 and data.get("eps") is None:
                        data["eps"] = clean_numeric(cells[col_eps])
                    if col_dps != -1 and data.get("dps") is None:
                        data["dps"] = clean_numeric(cells[col_dps])
                if col_pub != -1 and len(cells) > col_pub:
                    val_pub = cells[col_pub].strip()
                    if val_pub and val_pub not in ["-", "－", "―", "発表日"]:
                        m = re.search(r"(\d{2,4})[-/](\d{1,2})[-/](\d{1,2})", val_pub)
                        if m:
                            year_part = m.group(1)
                            if len(year_part) == 2:
                                year_part = f"20{year_part}"
                            month_part = m.group(2).zfill(2)
                            day_part = m.group(3).zfill(2)
                            fallback_earnings_date = f"{year_part}-{month_part}-{day_part}"

    # 11. 決算発表日
    earnings_date = None
    for td_th in soup.find_all(["td", "th", "span", "div", "dt", "dd", "tr"]):
        text = td_th.get_text(strip=True)
        if "決算発表日" in text or "決算予定" in text or "発表予定" in text:
            m = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})", text)
            if m:
                earnings_date = m.group(1).replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
                break
            sibling = td_th.find_next_sibling()
            if sibling:
                sibling_text = sibling.get_text(strip=True)
                m = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})|(\d{1,2}[-/月]\d{1,2})", sibling_text)
                if m:
                    earnings_date = m.group(0).replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
                    break
    data["earnings_date"] = earnings_date or fallback_earnings_date

    # 12. 株主優待の有無
    has_yutai = "なし"
    for a in soup.find_all("a"):
        href = a.get("href", "")
        a_text = a.get_text()
        if "yutai" in href or "株主優待" in a_text:
            if "code=" in href or "優待" in a_text:
                has_yutai = "あり"
                break
    if has_yutai == "なし" and "株主優待" in soup.get_text() and "優待情報" in soup.get_text():
        has_yutai = "あり"
    data["has_yutai"] = has_yutai

    # 13. VWAP
    vwap = None
    for td_th in soup.find_all(["td", "th", "span"]):
        text = td_th.get_text(strip=True)
        if "VWAP" in text:
            sibling = td_th.find_next_sibling()
            if sibling:
                vwap = clean_numeric(sibling.get_text(strip=True))
                break
    if vwap is None:
        for tr in soup.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) >= 2 and "VWAP" in cells[0]:
                vwap = clean_numeric(cells[1])
                break
    data["vwap"] = vwap

    # 14. 業種
    sector = None
    for tr in soup.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th and td and "業種" in th.get_text(strip=True):
            sector = td.get_text(strip=True)
            break
    data["sector"] = sector

    # 15. 決算月
    settlement_month = None
    for table in soup.find_all("table"):
        first_tr = table.find("tr")
        if not first_tr:
            continue
        headers = [th.get_text(strip=True) for th in first_tr.find_all(["th", "td"])]
        headers_cleaned = [h.replace("１", "1").replace("　", "").strip() for h in headers]
        if "決算期" in headers_cleaned and ("1株益" in headers_cleaned or "1株配" in headers_cleaned):
            for tr in table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) > 0 and "予" in cells[0]:
                    m = re.search(r"\d{2,4}\.(\d{2})", cells[0])
                    if m:
                        settlement_month = int(m.group(1))
                        break
            if settlement_month:
                break
    data["settlement_month"] = settlement_month

    if data["price"] is None and data["name"] and data["name"] != "Unknown":
        if "(上場廃止" not in data["name"]:
            data["name"] += " (上場廃止?)"

    return data


def parse_stock_history(html: str) -> list[dict]:
    """株探の日足株価履歴ページを解析する(date, open, high, low, price, volume)"""
    if not html:
        return []
    if is_rate_limited(html):
        return [{"error": "rate_limit_blocked"}]

    soup = BeautifulSoup(html, "html.parser")
    records = []
    seen_dates = set()

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all(["th"])]
        if "始値" in headers and "終値" in headers:
            trs = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
            for tr in trs:
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) >= 5:
                    raw_date = cells[0]
                    m_date = re.match(r"^(\d{2})/(\d{2})/(\d{2})$", raw_date)
                    if not m_date:
                        continue
                    date_str = f"20{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}"
                    if date_str not in seen_dates:
                        seen_dates.add(date_str)
                        records.append({
                            "date": date_str,
                            "open": clean_numeric(cells[1]),
                            "high": clean_numeric(cells[2]),
                            "low": clean_numeric(cells[3]),
                            "price": clean_numeric(cells[4]),
                            "volume": clean_numeric(cells[7]) if len(cells) >= 8 else None,
                        })

    records.sort(key=lambda x: x["date"], reverse=True)
    return records


def parse_margin_history(html: str) -> list[dict]:
    """株探の週次信用残高履歴ページを解析する"""
    if not html:
        return []
    if is_rate_limited(html):
        return [{"error": "rate_limit_blocked"}]

    soup = BeautifulSoup(html, "html.parser")

    margin_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all(["th"])]
        if "売り残(株)" in headers or "買い残(株)" in headers:
            margin_table = table
            break
    if not margin_table:
        return []

    records = []
    # 列: ['日付', '終値', '前週比率', '売買単価', '売買高(株)', '売り残(株)', '買い残(株)', '信用倍率']
    trs = margin_table.find("tbody").find_all("tr") if margin_table.find("tbody") else margin_table.find_all("tr")[1:]
    for tr in trs:
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) >= 8:
            raw_date = cells[0]
            m_date = re.match(r"^(\d{2})/(\d{2})/(\d{2})$", raw_date)
            if not m_date:
                continue
            date_str = f"20{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}"
            records.append({
                "date": date_str,
                "margin_sell": clean_numeric(cells[5]),
                "margin_buy": clean_numeric(cells[6]),
                "margin_ratio": clean_numeric(cells[7]),
            })

    for i in range(len(records) - 1):
        curr, prev = records[i], records[i + 1]
        curr["margin_sell_change"] = (
            curr["margin_sell"] - prev["margin_sell"]
            if curr["margin_sell"] is not None and prev["margin_sell"] is not None
            else None
        )
        curr["margin_buy_change"] = (
            curr["margin_buy"] - prev["margin_buy"]
            if curr["margin_buy"] is not None and prev["margin_buy"] is not None
            else None
        )
    if records:
        records[-1]["margin_sell_change"] = None
        records[-1]["margin_buy_change"] = None

    return records


def _parse_period_key(item: dict) -> int:
    p = re.sub(r"[^\d\.]", "", item["period"])
    parts = p.split(".")
    if len(parts) == 2:
        try:
            year = int(parts[0])
            month = int(parts[1])
            if year < 100:
                year += 2000 if year > 50 else 1900
            return year * 100 + month
        except ValueError:
            pass
    return 0


def parse_finance_details(finance_html: str, data: dict) -> dict:
    """株探の決算ページ(/stock/finance)を解析し、bps・roe・equity_ratio・決算進捗率・CAGR等を
    data辞書に書き込んで返す(parse_top_pageの結果とマージして使う)。"""
    if not finance_html or data is None:
        return data

    soup = BeautifulSoup(finance_html, "html.parser")

    # BPS・自己資本比率
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).replace("　", "").strip() for th in table.find_all(["th", "td"])]
        if "１株純資産" in headers and "自己資本比率" in headers:
            col_bps = headers.index("１株純資産")
            col_equity = headers.index("自己資本比率")
            trs = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
            latest_tr = None
            for tr in reversed(trs):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) > max(col_bps, col_equity) and clean_numeric(cells[col_bps]) is not None:
                    latest_tr = tr
                    break
            if latest_tr:
                cells = [c.get_text(strip=True) for c in latest_tr.find_all(["td", "th"])]
                data["bps"] = clean_numeric(cells[col_bps])
                data["equity_ratio"] = clean_numeric(cells[col_equity])

    # ROE
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all(["th"])]
        headers_cleaned = [h.replace("ＲＯＥ", "ROE").replace("　ＲＯＥ", "ROE").strip() for h in headers]
        if "ROE" in headers_cleaned:
            col_idx = headers_cleaned.index("ROE")
            trs = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
            latest_val = None
            for tr in trs:
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) > col_idx:
                    val = clean_numeric(cells[col_idx])
                    if val is not None and "予" not in cells[0]:
                        latest_val = val
            if latest_val is not None:
                data["roe"] = latest_val

    # 経常益進捗率(会社予想 vs 直近四半期累計)
    forecast_ord_income = None
    for table in soup.find_all("table"):
        first_tr = table.find("tr")
        if not first_tr:
            continue
        headers = [th.get_text(strip=True) for th in first_tr.find_all(["th", "td"])]
        headers_cleaned = [h.replace("　", "").replace(" ", "").strip() for h in headers]
        if "決算期" not in headers_cleaned:
            continue
        col_idx = -1
        if "経常益" in headers_cleaned:
            col_idx = headers_cleaned.index("経常益")
        elif "経常利益" in headers_cleaned:
            col_idx = headers_cleaned.index("経常利益")
        if col_idx != -1 and "前年比" in headers_cleaned:
            trs = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
            for tr in trs:
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) > col_idx and "予" in cells[0]:
                    val = clean_numeric(cells[col_idx])
                    if val is not None and val > 0:
                        forecast_ord_income = val
                        break

    cumulative_ord_income = None
    period_text = None
    for table in soup.find_all("table"):
        first_tr = table.find("tr")
        if not first_tr:
            continue
        headers = [th.get_text(strip=True) for th in first_tr.find_all(["th", "td"])]
        headers_cleaned = [h.replace("　", "").replace(" ", "").strip() for h in headers]
        if "決算期" not in headers_cleaned or "修正1株配" not in headers_cleaned:
            continue
        col_idx = -1
        if "経常益" in headers_cleaned:
            col_idx = headers_cleaned.index("経常益")
        elif "経常利益" in headers_cleaned:
            col_idx = headers_cleaned.index("経常利益")
        if col_idx == -1:
            continue
        trs = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
        for tr in reversed(trs):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) > col_idx and "予" not in cells[0] and "前年同期比" not in cells[0] and "-" in cells[0]:
                val = clean_numeric(cells[col_idx])
                if val is not None and cells[col_idx].strip() != "－":
                    cumulative_ord_income = val
                    period_text = cells[0]
                    break
        if cumulative_ord_income is not None:
            break

    data["progress_rate"] = None
    data["progress_quarter"] = None
    data["progress_excess"] = None

    if forecast_ord_income and cumulative_ord_income and period_text:
        m = re.search(r"(\d{1,2})-(\d{1,2})", period_text)
        if m:
            start_m, end_m = int(m.group(1)), int(m.group(2))
            diff = (end_m - start_m + 1) if end_m >= start_m else (end_m + 12 - start_m + 1)
            quarter_label = {3: "1Q", 6: "2Q", 9: "3Q"}.get(diff)
            threshold = {3: 25.0, 6: 50.0, 9: 75.0}.get(diff)
            if quarter_label:
                progress_rate = (cumulative_ord_income / forecast_ord_income) * 100.0
                data["progress_rate"] = round(progress_rate, 2)
                data["progress_quarter"] = quarter_label
                data["progress_excess"] = round(progress_rate - threshold, 2)

    # 売上高・経常益の年次推移 → CAGR・連続増収増益
    annual_history = []
    for table in soup.find_all("table"):
        first_tr = table.find("tr")
        if not first_tr:
            continue
        headers = [th.get_text(strip=True).replace("　", "").replace(" ", "") for th in first_tr.find_all(["th", "td"])]
        if "決算期" in headers and "売上高" in headers and "経常益" in headers:
            col_rev = headers.index("売上高")
            col_prof = headers.index("経常益")
            trs = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
            for tr in trs:
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) > max(col_rev, col_prof):
                    period = cells[0]
                    if period and "予" not in period and "会" not in period and "売上高" not in period and "決算期" not in period:
                        rev = clean_numeric(cells[col_rev])
                        prof = clean_numeric(cells[col_prof])
                        if rev is not None and prof is not None:
                            annual_history.append({"period": period, "revenue": rev, "profit": prof})
            if annual_history:
                break

    data["cagr_revenue_3y"] = None
    data["cagr_revenue_5y"] = None
    data["cagr_profit_3y"] = None
    data["cagr_profit_5y"] = None
    data["consecutive_revenue_growth"] = 0
    data["consecutive_profit_growth"] = 0

    annual_history = [x for x in annual_history if _parse_period_key(x) > 0]
    annual_history.sort(key=_parse_period_key)

    if len(annual_history) >= 2:
        latest = annual_history[-1]
        if len(annual_history) >= 4:
            three_years_ago = annual_history[-4]
            if three_years_ago["revenue"] > 0 and latest["revenue"] > 0:
                data["cagr_revenue_3y"] = round(((latest["revenue"] / three_years_ago["revenue"]) ** (1 / 3) - 1) * 100, 2)
            if three_years_ago["profit"] > 0 and latest["profit"] > 0:
                data["cagr_profit_3y"] = round(((latest["profit"] / three_years_ago["profit"]) ** (1 / 3) - 1) * 100, 2)
        if len(annual_history) >= 6:
            five_years_ago = annual_history[-6]
            if five_years_ago["revenue"] > 0 and latest["revenue"] > 0:
                data["cagr_revenue_5y"] = round(((latest["revenue"] / five_years_ago["revenue"]) ** (1 / 5) - 1) * 100, 2)
            if five_years_ago["profit"] > 0 and latest["profit"] > 0:
                data["cagr_profit_5y"] = round(((latest["profit"] / five_years_ago["profit"]) ** (1 / 5) - 1) * 100, 2)

        consec_rev = 0
        for idx in range(len(annual_history) - 1, 0, -1):
            if annual_history[idx]["revenue"] > annual_history[idx - 1]["revenue"]:
                consec_rev += 1
            else:
                break
        consec_prof = 0
        for idx in range(len(annual_history) - 1, 0, -1):
            if annual_history[idx]["profit"] > annual_history[idx - 1]["profit"]:
                consec_prof += 1
            else:
                break
        data["consecutive_revenue_growth"] = consec_rev
        data["consecutive_profit_growth"] = consec_prof

    return data


def parse_per_history(html: str) -> list[dict]:
    """個別銘柄のPER履歴ページを解析する(date, per)"""
    if not html:
        return []
    if is_rate_limited(html):
        return [{"error": "rate_limit_blocked"}]

    soup = BeautifulSoup(html, "html.parser")

    target_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all(["th"])]
        if not headers:
            tr_first = table.find("tr")
            if tr_first:
                headers = [td.get_text(strip=True) for td in tr_first.find_all(["td", "th"])]
        if ("日付" in headers or any("日付" in h for h in headers)) and ("PER" in headers or any("PER" in h for h in headers)):
            target_table = table
            break
    if not target_table:
        return []

    records = []
    trs = target_table.find("tbody").find_all("tr") if target_table.find("tbody") else target_table.find_all("tr")[1:]
    for tr in trs:
        cells = tr.find_all(["td", "th"])
        if len(cells) >= 3:
            time_tag = cells[0].find("time")
            raw_date = time_tag.get_text(strip=True) if time_tag else cells[0].get_text(strip=True)

            date_str = None
            if time_tag and time_tag.has_attr("datetime"):
                date_str = time_tag["datetime"].strip()
            else:
                m_date = re.match(r"^(\d{2,4})[-/](\d{1,2})[-/](\d{1,2})$", raw_date)
                if m_date:
                    year, month, day = m_date.group(1), m_date.group(2).zfill(2), m_date.group(3).zfill(2)
                    if len(year) == 2:
                        year = f"20{year}"
                    date_str = f"{year}-{month}-{day}"
                else:
                    m_month = re.match(r"^(\d{2,4})[-/](\d{1,2})$", raw_date)
                    if m_month:
                        year, month = m_month.group(1), m_month.group(2).zfill(2)
                        if len(year) == 2:
                            year = f"20{year}"
                        date_str = f"{year}-{month}-01"
            if not date_str:
                continue

            records.append({"date": date_str, "per": clean_numeric(cells[2].get_text(strip=True))})

    return records
