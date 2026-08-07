import sqlite3
import threading
import pandas as pd
from pathlib import Path

from utils import cloud_store

_DATA_DIR = Path(__file__).parent.parent / "data"
_DB_PATHS = {
    "default": _DATA_DIR / "portfolio.db",
    "yurong":  _DATA_DIR / "portfolio_yurong.db",
}
_local = threading.local()

# 這三張表就是使用者的全部資料，整包同步到 Firestore
_SYNC_TABLES = ["holdings", "capital", "trade_history", "alerts"]

# 向後相容
DB_PATH = _DB_PATHS["default"]


def set_user(user: str):
    """切換當前執行緒使用的 DB。在 Streamlit 每個 session 開頭呼叫一次。"""
    _local.db_path = _DB_PATHS.get(user, _DB_PATHS["default"])
    _local.user = user


def _current_user() -> str:
    return getattr(_local, "user", "default")


def _export_state(conn) -> dict:
    state = {}
    for t in _SYNC_TABLES:
        try:
            state[t] = pd.read_sql(f"SELECT * FROM {t}", conn).to_dict(orient="records")
        except Exception:
            state[t] = []
    return state


def _sync_up():
    """任何寫入之後把整包狀態推上 Firestore。雲端容器重啟才不會清空。"""
    if not cloud_store.is_enabled():
        return
    try:
        with get_connection() as conn:
            cloud_store.save_state(_current_user(), _export_state(conn))
    except Exception:
        pass


def _sync_down():
    """開站時把 Firestore 的狀態灌回本地 SQLite（雲端為準）。"""
    if not cloud_store.is_enabled():
        return
    state = cloud_store.load_state(_current_user())
    if not state:
        # 雲端還沒有資料：把本地現有的推上去當第一版，避免第一次就被空白蓋掉
        _sync_up()
        return
    try:
        with get_connection() as conn:
            for t in _SYNC_TABLES:
                rows = state.get(t) or []
                conn.execute(f"DELETE FROM {t}")
                if not rows:
                    continue
                cols = list(rows[0].keys())
                conn.executemany(
                    f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [tuple(r.get(c) for c in cols) for r in rows],
                )
    except Exception:
        pass


def get_connection():
    db_path = getattr(_local, "db_path", _DB_PATHS["default"])
    db_path.parent.mkdir(exist_ok=True)
    return sqlite3.connect(db_path)

def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id TEXT NOT NULL,
                stock_name TEXT,
                buy_price REAL NOT NULL,
                shares REAL NOT NULL,          -- 用「股」存，1張=1000股
                unit TEXT DEFAULT '股',         -- '張' 或 '股'（顯示用）
                buy_date TEXT NOT NULL,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # 舊表升級：補 unit 欄位
        try:
            conn.execute("ALTER TABLE holdings ADD COLUMN unit TEXT DEFAULT '股'")
        except Exception:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS capital (
                id INTEGER PRIMARY KEY,
                total_invested REAL DEFAULT 0,
                available_cash REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,   -- 已實現損益（賣出結算）
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # 舊表升級
        try:
            conn.execute("ALTER TABLE capital ADD COLUMN realized_pnl REAL DEFAULT 0")
        except Exception:
            pass

        conn.execute("""
            INSERT OR IGNORE INTO capital (id, total_invested, available_cash, realized_pnl)
            VALUES (1, 0, 50000, 0)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id TEXT,
                stock_name TEXT,
                action TEXT,            -- 'BUY' or 'SELL'
                price REAL,
                shares REAL,
                unit TEXT,
                pnl REAL DEFAULT 0,     -- 此次損益（賣出才有）
                trade_date TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id TEXT,
                alert_type TEXT,
                message TEXT,
                level TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                is_read INTEGER DEFAULT 0
            )
        """)

    _sync_down()


def _to_shares(qty: float, unit: str) -> float:
    """統一轉換為「股」數"""
    return qty * 1000 if unit == "張" else qty


def _display_qty(shares: float, unit: str) -> str:
    """顯示用：依單位回傳易讀字串"""
    if unit == "張":
        lots = shares / 1000
        remainder = int(shares) % 1000
        if remainder == 0:
            return f"{lots:.0f} 張"
        else:
            return f"{int(shares//1000)} 張 {remainder} 股"
    else:
        return f"{int(shares)} 股"


def get_holdings() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM holdings ORDER BY buy_date DESC", conn)


def add_holding(stock_id: str, stock_name: str, buy_price: float,
                qty: float, unit: str, buy_date: str, note: str = ""):
    """
    qty: 輸入的數量（張或股）
    unit: '張' or '股'
    """
    shares = _to_shares(qty, unit)          # 統一存股
    cost = buy_price * shares               # 總成本

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO holdings (stock_id, stock_name, buy_price, shares, unit, buy_date, note) "
            "VALUES (?,?,?,?,?,?,?)",
            (stock_id, stock_name, buy_price, shares, unit, buy_date, note)
        )
        conn.execute(
            "UPDATE capital SET total_invested=total_invested+?, available_cash=available_cash-?, "
            "updated_at=datetime('now','localtime') WHERE id=1",
            (cost, cost)
        )
        conn.execute(
            "INSERT INTO trade_history (stock_id, stock_name, action, price, shares, unit, trade_date) "
            "VALUES (?,?,?,?,?,?,?)",
            (stock_id, stock_name, "BUY", buy_price, shares, unit, buy_date)
        )
    _sync_up()


def sell_holding(holding_id: int, sell_price: float, sell_qty: float, sell_unit: str,
                 sell_date: str) -> dict:
    """
    賣出持股（支援部分賣出）
    回傳: {"pnl": 損益金額, "proceeds": 賣出所得}
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT stock_id, stock_name, buy_price, shares, unit FROM holdings WHERE id=?",
            (holding_id,)
        ).fetchone()
        if not row:
            return {"pnl": 0, "proceeds": 0, "error": "找不到持股"}

        sid, sname, buy_price, total_shares, orig_unit = row
        sell_shares = _to_shares(sell_qty, sell_unit)

        if sell_shares > total_shares + 0.001:
            return {"pnl": 0, "proceeds": 0, "error": f"賣出數量({sell_shares}股)超過持有({total_shares}股)"}

        proceeds = sell_price * sell_shares
        cost = buy_price * sell_shares
        pnl = proceeds - cost

        # 更新持倉
        remaining = total_shares - sell_shares
        if remaining < 0.5:
            conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
        else:
            conn.execute("UPDATE holdings SET shares=? WHERE id=?", (remaining, holding_id))

        # 更新資金表
        conn.execute(
            "UPDATE capital SET "
            "  available_cash=available_cash+?, "
            "  total_invested=total_invested-?, "
            "  realized_pnl=realized_pnl+?, "
            "  updated_at=datetime('now','localtime') "
            "WHERE id=1",
            (proceeds, cost, pnl)
        )

        # 記錄交易歷史
        conn.execute(
            "INSERT INTO trade_history (stock_id, stock_name, action, price, shares, unit, pnl, trade_date) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, sname, "SELL", sell_price, sell_shares, sell_unit, pnl, sell_date)
        )

        result = {"pnl": pnl, "proceeds": proceeds, "remaining_shares": remaining}

    _sync_up()
    return result


def remove_holding(holding_id: int):
    """直接移除（不計損益，用於錯誤修正）"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT buy_price, shares FROM holdings WHERE id=?", (holding_id,)
        ).fetchone()
        if row:
            cost = row[0] * row[1]
            conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
            conn.execute(
                "UPDATE capital SET available_cash=available_cash+?, "
                "total_invested=total_invested-?, updated_at=datetime('now','localtime') WHERE id=1",
                (cost, cost)
            )
    _sync_up()


def get_capital() -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM capital WHERE id=1").fetchone()
        return {
            "total_invested": row[1],
            "available_cash": row[2],
            "realized_pnl": row[3] if len(row) > 3 else 0
        }


def update_available_cash(amount: float):
    with get_connection() as conn:
        conn.execute(
            "UPDATE capital SET available_cash=?, updated_at=datetime('now','localtime') WHERE id=1",
            (amount,)
        )
    _sync_up()


def get_trade_history() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT * FROM trade_history ORDER BY created_at DESC LIMIT 50", conn
        )


def add_alert(stock_id, alert_type, message, level="warning"):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (stock_id, alert_type, message, level) VALUES (?,?,?,?)",
            (stock_id, alert_type, message, level)
        )


def get_unread_alerts() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT * FROM alerts WHERE is_read=0 ORDER BY created_at DESC LIMIT 20", conn
        )


def mark_alerts_read():
    with get_connection() as conn:
        conn.execute("UPDATE alerts SET is_read=1")
