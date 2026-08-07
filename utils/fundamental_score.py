"""
基本面評分（/20）
來源：TWSE OpenAPI（官方、免 token、不限次），FinMind 只當備援。

改用 TWSE 的原因：FinMind 匿名額度極低，一輪選股就把額度打爆，
基本面分數會整批變成 0，畫面顯示「資料不足」。TWSE OpenAPI 沒有這個問題。

計算：月營收年增率 /8、毛利率 /6、稅後淨利率 /6
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import urllib3

from utils.stock_data import _cache_read, _cache_write

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TWSE_OPENAPI = "https://openapi.twse.com.tw/v1/opendata"

# 綜合損益表依產業別拆成五張表，全部合併才能涵蓋所有上市公司
INCOME_DATASETS = [
    "t187ap06_L_ci",    # 一般業
    "t187ap06_L_fh",    # 金控業
    "t187ap06_L_ins",   # 保險業
    "t187ap06_L_bd",    # 證券期貨業
    "t187ap06_L_mim",   # 異業
]

FINMIND_API = "https://api.finmindtrade.com/api/v4/data"


def _twse_open(dataset: str, ttl: int = 43200) -> pd.DataFrame:
    """抓 TWSE OpenAPI 並落地快取（半天）。全市場一次抓完，逐檔查表不再打網路。"""
    key = f"TWSE_OPENAPI|{dataset}"
    cached, is_stale = _cache_read(key, max_age=ttl)
    if cached is not None and not is_stale:
        return cached
    try:
        r = requests.get(f"{TWSE_OPENAPI}/{dataset}",
                         headers={"accept": "application/json", "User-Agent": "Mozilla/5.0"},
                         timeout=20, verify=False)
        data = r.json()
        if isinstance(data, list) and data:
            df = pd.DataFrame(data)
            _cache_write(key, df)
            return df
    except Exception:
        pass
    return cached if cached is not None else pd.DataFrame()


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def get_monthly_revenue(stock_id: str) -> dict:
    """月營收：TWSE 已直接提供年增率與累計年增率，不必自己算。"""
    df = _twse_open("t187ap05_L")
    if df.empty or "公司代號" not in df.columns:
        return {}
    hit = df[df["公司代號"].astype(str).str.strip() == stock_id]
    if hit.empty:
        return {}
    r = hit.iloc[0]
    return {
        "month": str(r.get("資料年月", "")),
        "revenue": _num(r.get("營業收入-當月營收")),
        "yoy": _num(r.get("營業收入-去年同月增減(%)")),
        "mom": _num(r.get("營業收入-上月比較增減(%)")),
        "cum_yoy": _num(r.get("累計營業收入-前期比較增減(%)")),
        "note": str(r.get("備註", "")).strip(),
    }


def get_income_statement(stock_id: str) -> dict:
    """最新一季綜合損益表（五張產業表合併查）。"""
    for ds in INCOME_DATASETS:
        df = _twse_open(ds)
        if df.empty or "公司代號" not in df.columns:
            continue
        hit = df[df["公司代號"].astype(str).str.strip() == stock_id]
        if hit.empty:
            continue
        r = hit.iloc[0]
        rev = _num(r.get("營業收入"))
        cost = _num(r.get("營業成本"))
        gross = _num(r.get("營業毛利（毛損）淨額")) or _num(r.get("營業毛利（毛損）"))
        if not gross and rev:
            gross = rev - cost
        net = _num(r.get("淨利（淨損）歸屬於母公司業主")) or _num(r.get("本期淨利（淨損）"))
        return {
            "year": str(r.get("年度", "")), "quarter": str(r.get("季別", "")),
            "revenue": rev,
            "gross_margin": (gross / rev * 100) if rev else None,
            "net_margin": (net / rev * 100) if rev else None,
            "eps": _num(r.get("基本每股盈餘（元）")),
        }
    return {}


def _finmind_revenue_fallback(stock_id: str) -> float:
    """TWSE 查不到（例如興櫃／剛上市）時才走 FinMind，回傳近三月 YoY%。"""
    try:
        start = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")
        r = requests.get(FINMIND_API, params={
            "dataset": "TaiwanStockMonthRevenue", "data_id": stock_id, "start_date": start,
        }, timeout=10, verify=False)
        d = r.json()
        if d.get("status") != 200 or not d.get("data"):
            return None
        df = pd.DataFrame(d["data"]).sort_values("date")
        if len(df) < 15:
            return None
        rev = df["revenue"].astype(float)
        recent3, yoy3 = rev.tail(3).mean(), rev.iloc[-15:-12].mean()
        return (recent3 - yoy3) / yoy3 * 100 if yoy3 > 0 else None
    except Exception:
        return None


def get_fundamental_score(stock_id: str) -> dict:
    """
    基本面評分（/20）
    - 月營收年增率  /8
    - 毛利率        /6
    - 稅後淨利率    /6
    """
    score = 0
    details = {}

    # ── 月營收年增率 ────────────────────────────────────
    rev = get_monthly_revenue(stock_id)
    yoy = rev.get("yoy") if rev else None
    if yoy is None:
        yoy = _finmind_revenue_fallback(stock_id)

    if yoy is not None:
        label = f"月營收YoY {yoy:+.1f}%"
        if rev.get("month"):
            label = f"{rev['month']} 月營收YoY {yoy:+.1f}%"
        if yoy >= 20:
            score += 8
            details[label] = "✅ +8（高成長）"
        elif yoy >= 5:
            score += 5
            details[label] = "🟡 +5（穩定成長）"
        elif yoy >= -5:
            score += 2
            details[label] = "⚪ +2（持平）"
        else:
            details[label] = "❌ 0（衰退）"
    else:
        details["月營收"] = "⚪ 尚未公布"

    # ── 毛利率 / 淨利率 ─────────────────────────────────
    inc = get_income_statement(stock_id)
    gm = inc.get("gross_margin") if inc else None
    if gm is not None:
        tag = f"{inc['year']}Q{inc['quarter']} 毛利率 {gm:.1f}%"
        if gm >= 50:
            score += 6
            details[tag] = "✅ +6（高毛利）"
        elif gm >= 30:
            score += 4
            details[tag] = "🟡 +4（中毛利）"
        elif gm >= 15:
            score += 2
            details[tag] = "⚪ +2（低毛利）"
        else:
            details[tag] = "❌ 0（毛利偏低）"
    else:
        details["毛利率"] = "⚪ 本季財報尚未公布"

    nm = inc.get("net_margin") if inc else None
    if nm is not None:
        tag = f"稅後淨利率 {nm:.1f}%"
        if inc.get("eps"):
            tag += f"（EPS {inc['eps']:.2f}）"
        if nm >= 15:
            score += 6
            details[tag] = "✅ +6"
        elif nm >= 5:
            score += 3
            details[tag] = "🟡 +3"
        elif nm > 0:
            score += 1
            details[tag] = "⚪ +1（微利）"
        else:
            details[tag] = "❌ 0（虧損）"
    else:
        details["淨利率"] = "⚪ 本季財報尚未公布"

    return {"score": min(score, 20), "details": details}
