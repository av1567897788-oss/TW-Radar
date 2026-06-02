"""
TW-Radar 選股引擎
- 推薦股：五層評分篩選，由高到低排列
- 堪憂股：偵測技術面惡化 + 籌碼逃跑訊號

資料來源：FinMind（真實資料，不幻想）
"""

import pandas as pd
from datetime import datetime
from utils.stock_data import get_stock_price, get_institutional, compute_technical_score, get_chip_score

# ── 台股核心監控清單（主要大型股+熱門股）──────────────────
WATCH_LIST = [
    # 半導體/AI
    ("2330", "台積電"), ("2303", "聯電"), ("2454", "聯發科"),
    ("3034", "聯詠"), ("6770", "力積電"), ("2337", "旺宏"),
    # AI伺服器/散熱
    ("3231", "緯創"), ("2382", "廣達"), ("2356", "英業達"),
    ("2317", "鴻海"), ("6669", "緯穎"), ("3017", "奇鋐"),
    # 金融
    ("2881", "富邦金"), ("2882", "國泰金"), ("2891", "中信金"),
    ("2886", "兆豐金"), ("2884", "玉山金"),
    # 傳產/電動車
    ("2002", "中鋼"), ("1301", "台塑"), ("1303", "南亞"),
    ("2207", "和泰車"), ("6953", "緯湃"),
    # 電信/網路
    ("2412", "中華電"), ("3045", "台灣大"), ("4904", "遠傳"),
]


def score_stock(stock_id: str, stock_name: str) -> dict:
    """
    對單支股票跑技術面 + 籌碼面評分
    回傳完整評分資料，若無資料則回傳 None
    """
    try:
        price_df = get_stock_price(stock_id, days=90)
        chip_df = get_institutional(stock_id, days=30)

        if price_df.empty or len(price_df) < 20:
            return None

        tech = compute_technical_score(price_df)
        chip = get_chip_score(chip_df)

        tech_score = tech.get("score") or 0
        chip_score = chip.get("score") or 0

        # 目前只有兩層有資料（技術面+籌碼面），各佔25%
        # 其餘三層（基本面/產業面/AI先知）留空，等後續版本
        total = tech_score + chip_score  # 最高40分（20+20）

        current = tech.get("current", 0)
        rsi = tech.get("rsi", 50)

        return {
            "stock_id": stock_id,
            "stock_name": stock_name,
            "total_score": total,
            "tech_score": tech_score,
            "chip_score": chip_score,
            "current_price": current,
            "rsi": rsi,
            "ma5": tech.get("ma5", 0),
            "ma20": tech.get("ma20", 0),
            "tech_details": tech.get("details", {}),
            "chip_details": chip.get("details", {}),
        }
    except Exception:
        return None


def get_recommended_stocks(top_n: int = 10) -> list:
    """
    推薦股：技術面+籌碼面評分最高的前N支
    評分標準全部基於真實 FinMind 資料
    """
    results = []
    for sid, sname in WATCH_LIST:
        data = score_stock(sid, sname)
        if data:
            results.append(data)

    # 由高到低排序
    results.sort(key=lambda x: x["total_score"], reverse=True)

    # 加上推薦理由
    for i, r in enumerate(results[:top_n]):
        reasons = []
        if r["tech_score"] >= 16:
            reasons.append("技術面強勢")
        if r["chip_score"] >= 10:
            reasons.append("外資積極買入")
        if r["rsi"] and 40 <= r["rsi"] <= 65:
            reasons.append(f"RSI健康({r['rsi']:.0f})")
        elif r["rsi"] and r["rsi"] < 40:
            reasons.append(f"超賣反彈機會(RSI={r['rsi']:.0f})")
        r["reasons"] = reasons if reasons else ["多項指標達標"]
        r["rank"] = i + 1

    return results[:top_n]


def get_warning_stocks(holdings_df: pd.DataFrame = None) -> list:
    """
    堪憂股：兩類
    1. 監控清單中技術面惡化的股票
    2. 使用者持股中有問題的（優先顯示）
    """
    warnings = []

    # 先檢查使用者持股
    if holdings_df is not None and not holdings_df.empty:
        for _, row in holdings_df.iterrows():
            sid = row["stock_id"]
            sname = row.get("stock_name", sid) or sid
            buy_price = float(row["buy_price"])

            data = score_stock(sid, sname)
            if not data:
                continue

            current = data["current_price"]
            pnl_pct = (current - buy_price) / buy_price * 100 if buy_price else 0

            risk_reasons = []
            urgency = 0  # 數字越高越緊急

            if pnl_pct <= -8:
                risk_reasons.append(f"⛔ 已觸停損線（-{abs(pnl_pct):.1f}%）")
                urgency += 10
            elif pnl_pct <= -4:
                risk_reasons.append(f"⚠️ 接近停損線（-{abs(pnl_pct):.1f}%）")
                urgency += 5

            if data["tech_score"] <= 6:
                risk_reasons.append("技術面嚴重惡化")
                urgency += 4
            elif data["tech_score"] <= 10:
                risk_reasons.append("技術面轉弱")
                urgency += 2

            if data["rsi"] and data["rsi"] > 75:
                risk_reasons.append(f"RSI超買({data['rsi']:.0f})，回檔風險高")
                urgency += 3

            if data["chip_score"] is not None and data["chip_score"] <= 4:
                risk_reasons.append("外資賣超，籌碼流失")
                urgency += 3

            if risk_reasons:
                warnings.append({
                    **data,
                    "risk_reasons": risk_reasons,
                    "urgency": urgency,
                    "is_holding": True,
                    "buy_price": buy_price,
                    "pnl_pct": pnl_pct,
                    "action": "立即賣出" if urgency >= 8 else "考慮減碼" if urgency >= 4 else "密切觀察"
                })

    # 再掃描監控清單（不含已在持股的）
    holding_ids = set(holdings_df["stock_id"].tolist()) if holdings_df is not None and not holdings_df.empty else set()

    for sid, sname in WATCH_LIST:
        if sid in holding_ids:
            continue
        data = score_stock(sid, sname)
        if not data:
            continue

        risk_reasons = []
        urgency = 0

        if data["tech_score"] <= 4:
            risk_reasons.append("技術面崩壞（多頭排列全失守）")
            urgency += 5
        if data["rsi"] and data["rsi"] > 78:
            risk_reasons.append(f"RSI嚴重超買({data['rsi']:.0f})")
            urgency += 3
        if data["chip_score"] is not None and data["chip_score"] == 0:
            risk_reasons.append("外資連續大賣")
            urgency += 4

        if risk_reasons and urgency >= 4:
            warnings.append({
                **data,
                "risk_reasons": risk_reasons,
                "urgency": urgency,
                "is_holding": False,
                "buy_price": None,
                "pnl_pct": None,
                "action": "避免進場"
            })

    # 由緊急程度排序（持股優先）
    warnings.sort(key=lambda x: (not x["is_holding"], -x["urgency"]))
    return warnings[:15]
