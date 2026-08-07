import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import os
import json
import hashlib
from pathlib import Path

FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# FinMind 匿名呼叫每小時額度極低，一輪選股就會打爆（回 HTTP 402
# "Requests reach the upper limit"），舊版把這個錯誤靜默吞掉回傳空表，
# 畫面上就變成「資料不足」「資料不完整」。
# token 從環境變數或 .streamlit/secrets.toml 讀，不寫死在程式碼。
_CACHE_DIR = Path(__file__).parent.parent / "data" / "_api_cache"
_CACHE_TTL = 3600  # 秒；盤中一小時內同一筆不重複打

# 給 UI 讀的即時狀態，讓畫面能分辨「真的沒資料」與「被限流」
FINMIND_STATUS = {"rate_limited": False, "msg": "", "using_stale": False}

RELIABLE_SOURCES = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^TWII&region=TW&lang=zh-TW",
    "https://www.twse.com.tw/rss/",
]


def get_finmind_token() -> str:
    tok = os.environ.get("FINMIND_TOKEN", "")
    if tok:
        return tok
    try:
        import streamlit as st
        return st.secrets.get("FINMIND_TOKEN", "")
    except Exception:
        return ""


# 向後相容：舊程式碼有引用這個名字
FINMIND_TOKEN = get_finmind_token()


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".json")


def _cache_read(key: str, max_age: int = _CACHE_TTL):
    """回傳 (df, is_stale)；沒有快取回 (None, False)。"""
    p = _cache_path(key)
    if not p.exists():
        return None, False
    try:
        age = time.time() - p.stat().st_mtime
        df = pd.DataFrame(json.loads(p.read_text(encoding="utf-8")))
        return df, age > max_age
    except Exception:
        return None, False


def _cache_write(key: str, df: pd.DataFrame):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(
            df.to_json(orient="records", force_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def _fm_get(dataset: str, stock_id: str, start_date: str, end_date: str = None) -> pd.DataFrame:
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")

    cache_key = f"{dataset}|{stock_id}|{start_date}|{end_date}"
    cached, is_stale = _cache_read(cache_key)
    if cached is not None and not is_stale:
        return cached

    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    token = get_finmind_token()
    if token:
        params["token"] = token

    try:
        try:
            resp = requests.get(FINMIND_API, params=params, timeout=10)
        except requests.exceptions.SSLError:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.get(FINMIND_API, params=params, timeout=10, verify=False)

        data = resp.json()
        if data.get("status") == 200:
            FINMIND_STATUS.update({"rate_limited": False, "msg": "", "using_stale": False})
            df = pd.DataFrame(data["data"])
            _cache_write(cache_key, df)
            return df

        # 402 = 額度用盡。如實記錄，不要假裝成「查無資料」
        if resp.status_code == 402 or data.get("status") == 402:
            FINMIND_STATUS.update({
                "rate_limited": True,
                "msg": "FinMind 額度已用盡（未設定 token 時每小時上限極低），"
                       "請在 Secrets 加入 FINMIND_TOKEN",
            })
    except Exception as e:
        FINMIND_STATUS.update({"rate_limited": False, "msg": f"FinMind 連線失敗：{e}"[:120]})

    # 打不到就退回舊快取，寧可用昨天的資料，也不要整頁顯示「資料不足」
    if cached is not None:
        FINMIND_STATUS["using_stale"] = True
        return cached
    return pd.DataFrame()


def _twse_stock_day(stock_id: str, days: int) -> pd.DataFrame:
    """
    TWSE 官方日成交資料（免 token、限制寬鬆），FinMind 限流時的股價備援。
    回傳欄位對齊 FinMind：date / open / max / min / close / Trading_Volume
    """
    rows = []
    months = max(1, days // 30 + 2)
    cursor = datetime.today().replace(day=1)
    for _ in range(months):
        try:
            r = requests.get(
                "https://www.twse.com.tw/exchangeReport/STOCK_DAY",
                params={"response": "json", "date": cursor.strftime("%Y%m%d"), "stockNo": stock_id},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10, verify=False,
            )
            j = r.json()
            if j.get("stat") == "OK":
                for d in j.get("data", []):
                    try:
                        y, m, dd = d[0].split("/")
                        date = f"{int(y) + 1911}-{int(m):02d}-{int(dd):02d}"
                        rows.append({
                            "date": date,
                            "Trading_Volume": int(d[1].replace(",", "")),
                            "open": float(d[3].replace(",", "")),
                            "max": float(d[4].replace(",", "")),
                            "min": float(d[5].replace(",", "")),
                            "close": float(d[6].replace(",", "")),
                        })
                    except Exception:
                        continue
        except Exception:
            pass
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        time.sleep(0.3)  # TWSE 打太快會擋

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def get_stock_price(stock_id: str, days: int = 90) -> pd.DataFrame:
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    df = _fm_get("TaiwanStockPrice", stock_id, start)

    # FinMind 沒給資料就改打 TWSE 官方，技術面評分才不會整批變成「資料不足」
    if df.empty:
        cache_key = f"TWSE_STOCK_DAY|{stock_id}|{days}"
        cached, is_stale = _cache_read(cache_key)
        if cached is not None and not is_stale:
            df = cached
        else:
            df = _twse_stock_day(stock_id, days)
            if not df.empty:
                _cache_write(cache_key, df)
            elif cached is not None:
                df = cached

    if not df.empty and "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
    return df


def get_institutional(stock_id: str, days: int = 30) -> pd.DataFrame:
    """三大法人買賣超。FinMind 限流時改用 TWSE 全市場日報表回補。"""
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    df = _fm_get("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start)
    if df.empty:
        df = _twse_institutional_fallback(stock_id)
    return df


def _twse_institutional_fallback(stock_id: str, trading_days: int = 8) -> pd.DataFrame:
    """
    用 TWSE 每日全市場三大法人報表拼出單一檔股票的近期買賣超。
    每個日期的全市場報表只打一次並落地快取，選股掃描時所有股票共用，
    比逐檔打 FinMind 省掉幾十次請求。
    欄位對齊 get_chip_score 期待的 name / buy / sell / date。
    """
    rows = []
    cursor = datetime.today()
    checked = 0
    while checked < trading_days and (datetime.today() - cursor).days < 30:
        if cursor.weekday() < 5:
            date_str = cursor.strftime("%Y%m%d")
            key = f"TWSE_INST_ALL|{date_str}"
            cached, is_stale = _cache_read(key, max_age=86400 * 30)
            day_df = cached if cached is not None and not is_stale else None
            if day_df is None:
                day_df = get_twse_institutional_all(date_str)
                if not day_df.empty:
                    _cache_write(key, day_df)
                time.sleep(0.3)
            if not day_df.empty and "stock_id" in day_df.columns:
                hit = day_df[day_df["stock_id"] == stock_id]
                if not hit.empty:
                    r = hit.iloc[0]
                    iso = cursor.strftime("%Y-%m-%d")
                    for name, col in [("Foreign_Investor", "foreign_net"),
                                      ("Investment_Trust", "invest_net")]:
                        net = float(r.get(col, 0) or 0)
                        rows.append({
                            "date": iso, "stock_id": stock_id, "name": name,
                            "buy": max(net, 0.0), "sell": max(-net, 0.0),
                        })
                    checked += 1
        cursor -= timedelta(days=1)

    return pd.DataFrame(rows)


def get_margin_trading(stock_id: str, days: int = 30) -> pd.DataFrame:
    """融資融券"""
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    return _fm_get("TaiwanStockMarginPurchaseShortSale", stock_id, start)


def get_all_stock_names() -> dict:
    """全市場 代號→股名 對照表（TWSE 官方，免 token）。快取一天。"""
    key = "TWSE_NAME_MAP"
    cached, is_stale = _cache_read(key, max_age=86400)
    if cached is not None and not is_stale and not cached.empty:
        return dict(zip(cached["stock_id"], cached["stock_name"]))

    df = get_twse_all_stocks_today()
    if not df.empty and {"stock_id", "stock_name"} <= set(df.columns):
        out = df[["stock_id", "stock_name"]].drop_duplicates("stock_id").copy()
        out["stock_name"] = out["stock_name"].astype(str).str.strip()
        _cache_write(key, out)
        return dict(zip(out["stock_id"], out["stock_name"]))

    if cached is not None and not cached.empty:  # 過期也比沒有好
        return dict(zip(cached["stock_id"], cached["stock_name"]))
    return {}


def get_stock_info(stock_id: str) -> dict:
    """取得股票基本資訊（名稱）。FinMind 限流時退回 TWSE 對照表。"""
    name = get_all_stock_names().get(stock_id)
    if name:
        return {"name": name}
    try:
        resp = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params={"dataset": "TaiwanStockInfo", "token": get_finmind_token()},
            timeout=10, verify=False
        )
        data = resp.json()
        if data.get("status") == 200:
            df = pd.DataFrame(data["data"])
            row = df[df["stock_id"] == stock_id]
            if not row.empty:
                return {"name": row.iloc[0].get("stock_name", stock_id)}
    except Exception:
        pass
    return {"name": stock_id}


def get_twse_index(days: int = 60) -> dict:
    """
    加權指數（即時 + 歷史走勢）
    即時價：TWSE 官方 API
    歷史：用台積電2330走勢比例代替（FinMind無加權指數歷史）
    """
    result = {"price": None, "change": None, "change_pct": None, "history": pd.DataFrame()}

    # 即時價
    try:
        import requests as _req
        r = _req.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8, verify=False
        )
        item = r.json().get("msgArray", [{}])[0]
        z = float(item.get("z", 0) or 0)
        y = float(item.get("y", 0) or 0)
        if z > 0:
            result["price"] = z
            result["change"] = round(z - y, 2)
            result["change_pct"] = round((z - y) / y * 100, 2) if y else 0
    except Exception:
        pass

    # 歷史走勢用2330近似反映（台積電佔加權指數約27%）
    try:
        df = get_stock_price("2330", days)
        if not df.empty:
            result["history"] = df
    except Exception:
        pass

    return result


def compute_technical_score(df: pd.DataFrame) -> dict:
    """
    技術面評分（滿分20）
    只在有真實資料時計算，不幻想數值
    """
    if df.empty or len(df) < 20:
        return {"score": None, "reason": "資料不足，無法評分", "details": {}}

    close = df["close"].astype(float)
    volume = df["Trading_Volume"].astype(float) if "Trading_Volume" in df.columns else None

    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1] if len(df) >= 60 else None
    current = close.iloc[-1]

    score = 0
    details = {}

    # MA多頭排列
    if current > ma5:
        score += 4
        details["價格>MA5"] = "✅ +4"
    else:
        details["價格>MA5"] = "❌ 0"

    if ma5 > ma20:
        score += 4
        details["MA5>MA20"] = "✅ +4"
    else:
        details["MA5>MA20"] = "❌ 0"

    if ma60 and ma20 > ma60:
        score += 4
        details["MA20>MA60"] = "✅ +4"
    elif ma60 is None:
        details["MA20>MA60"] = "⚪ 資料不足"

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - 100 / (1 + rs)).iloc[-1]
    if 40 <= rsi <= 70:
        score += 4
        details[f"RSI={rsi:.1f}"] = "✅ +4（健康區間）"
    elif rsi < 40:
        score += 2
        details[f"RSI={rsi:.1f}"] = "🟡 +2（超賣，反彈機會）"
    else:
        details[f"RSI={rsi:.1f}"] = "❌ 0（超買，風險高）"

    # 量價配合
    if volume is not None and len(volume) >= 5:
        recent_vol = volume.iloc[-5:].mean()
        prev_vol = volume.iloc[-20:-5].mean() if len(volume) >= 20 else volume.mean()
        price_up = close.iloc[-1] > close.iloc[-5]
        if price_up and recent_vol > prev_vol:
            score += 4
            details["量增價漲"] = "✅ +4"
        else:
            details["量增價漲"] = "❌ 0"

    return {"score": min(score, 20), "rsi": round(rsi, 1), "details": details,
            "ma5": round(ma5, 2), "ma20": round(ma20, 2), "current": round(current, 2)}


def get_chip_score(institutional_df: pd.DataFrame) -> dict:
    """
    籌碼面評分（滿分20），僅用真實資料
    FinMind 欄位：name(Foreign_Investor/Investment_Trust/Dealer_self)，buy/sell
    """
    if institutional_df.empty:
        return {"score": 0, "reason": "無籌碼資料", "details": {"外資資料": "⚪ 無資料"}}

    score = 0
    details = {}

    # 外資：Foreign_Investor
    fi_names = ["Foreign_Investor", "外陸資買賣超股數(不含外資自營商)", "外資"]
    foreign = pd.DataFrame()
    for fn in fi_names:
        tmp = institutional_df[institutional_df["name"] == fn] if "name" in institutional_df.columns else pd.DataFrame()
        if not tmp.empty:
            foreign = tmp
            break

    if not foreign.empty:
        # 計算淨買超（buy - sell）
        if "buy" in foreign.columns and "sell" in foreign.columns:
            foreign = foreign.copy()
            foreign["net"] = foreign["buy"].astype(float) - foreign["sell"].astype(float)
            recent = foreign.sort_values("date").tail(5)["net"]
        elif "buy_sell" in foreign.columns:
            recent = foreign.sort_values("date").tail(5)["buy_sell"].astype(float)
        else:
            recent = pd.Series(dtype=float)

        if not recent.empty:
            consecutive_buy = (recent > 0).sum()
            net_5d = recent.sum()

            if consecutive_buy >= 4:
                score += 10
                details[f"外資連買{int(consecutive_buy)}日"] = "✅ +10"
            elif consecutive_buy >= 2:
                score += 5
                details[f"外資連買{int(consecutive_buy)}日"] = "🟡 +5"
            else:
                details["外資動向"] = "❌ 0（外資未積極買入）"

            if net_5d > 0:
                score += 5
                details[f"外資5日淨買{net_5d:+,.0f}股"] = "✅ +5"
            else:
                details[f"外資5日淨賣{net_5d:,.0f}股"] = "❌ 0"
        else:
            details["外資資料"] = "⚪ 計算失敗"
    else:
        details["外資資料"] = "⚪ 無法取得"

    # 投信：Investment_Trust
    it_names = ["Investment_Trust", "投信"]
    invest = pd.DataFrame()
    for fn in it_names:
        tmp = institutional_df[institutional_df["name"] == fn] if "name" in institutional_df.columns else pd.DataFrame()
        if not tmp.empty:
            invest = tmp
            break

    if not invest.empty and "buy" in invest.columns:
        invest = invest.copy()
        invest["net"] = invest["buy"].astype(float) - invest["sell"].astype(float)
        it_net = invest.sort_values("date").tail(3)["net"].sum()
        if it_net > 0:
            score += 5
            details[f"投信3日淨買{it_net:+,.0f}股"] = "✅ +5"

    return {"score": min(score, 20), "details": details}


# ── 全市場掃描工具（TWSE 官方 API，免費不限次）──────────────

def get_twse_all_stocks_today() -> pd.DataFrame:
    """
    TWSE 官方 API 一次取得全部上市股票今日行情。
    欄位：stock_id, stock_name, close, change, volume, pe_ratio
    非盤中（盤後）才有完整資料；盤中回傳空 DataFrame。
    """
    try:
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        r = requests.get(
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL",
            params={"response": "json", "date": today},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20, verify=False
        )

        # TWSE 這支已改成不論 response 參數都回 CSV，舊版當成 JSON 解析
        # 會直接丟例外被吞掉，全市場掃描與股名對照因此長期是空的。
        try:
            data = r.json()
            if data.get("stat") != "OK" or not data.get("data"):
                return pd.DataFrame()
            df = pd.DataFrame(data["data"], columns=data.get("fields", []))
        except ValueError:
            import io
            df = pd.read_csv(io.StringIO(r.text), dtype=str)
            df.columns = [str(c).strip().strip('"') for c in df.columns]
            for c in df.columns:
                df[c] = df[c].astype(str).str.strip().str.strip('"')

        # 標準化欄位名稱
        rename = {
            "證券代號": "stock_id",
            "證券名稱": "stock_name",
            "收盤價":   "close",
            "漲跌價差": "change",
            "成交股數": "volume",
            "本益比":   "pe_ratio",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        # 排除 ETF（代號開頭 00）與權證
        df = df[df["stock_id"].str.match(r"^\d{4}$", na=False)]
        # 數值轉換
        for col in ["close", "change", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
                )
        return df.dropna(subset=["close"]).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def get_twse_institutional_all(date_str: str = None) -> pd.DataFrame:
    """
    TWSE 三大法人買賣超（全市場，一次取得）。
    回傳 DataFrame：stock_id, foreign_net, invest_net, dealer_net, total_net
    """
    try:
        from datetime import datetime
        if not date_str:
            date_str = datetime.now().strftime("%Y%m%d")
        # T86 = 三大法人買賣超日報。舊版用的 TWT43U 欄名重複（多個「買進股數」），
        # pandas 會產生重複欄位讓後面的 rename 失效，整個籌碼面就變成 0 分。
        r = requests.get(
            "https://www.twse.com.tw/fund/T86",
            params={"response": "json", "date": date_str, "selectType": "ALLBUT0999"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15, verify=False
        )
        data = r.json()
        if data.get("stat") != "OK" or not data.get("data"):
            return pd.DataFrame()
        fields = data.get("fields", [])
        df = pd.DataFrame(data["data"], columns=fields)
        rename_map = {
            "證券代號": "stock_id",
            "證券名稱": "stock_name",
            "外陸資買賣超股數(不含外資自營商)": "foreign_net",
            "投信買賣超股數": "invest_net",
            "自營商買賣超股數": "dealer_net",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if "stock_id" not in df.columns:
            df = df.rename(columns={fields[0]: "stock_id"})
        df["stock_id"] = df["stock_id"].astype(str).str.strip()

        df = df[df["stock_id"].str.match(r"^\d{4}$", na=False)]
        for col in ["foreign_net", "invest_net", "dealer_net"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
                ).fillna(0)
        # 總淨買
        net_cols = [c for c in ["foreign_net", "invest_net", "dealer_net"] if c in df.columns]
        df["total_net"] = df[net_cols].sum(axis=1)
        return df.reset_index(drop=True)
    except Exception:
        return pd.DataFrame()
