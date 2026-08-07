"""
持股資料雲端保存（Firestore REST）

為什麼需要：Streamlit Cloud 的容器檔案系統是暫存的，每次重新部署／睡醒
（機器人每天推 simulation.db 就會觸發一次）都會把 data/portfolio.db 洗掉，
兩個人輸入的持股全部歸零。

做法：SQLite 仍然是工作用的資料庫（所有既有 SQL 邏輯不動），
開站時從 Firestore 拉一份回來蓋上去，每次寫入後再推上去。
每個使用者一份文件：tw_radar_users/{user}

憑證放在 Secrets 的 [firestore] 區塊（service account JSON 的欄位）。
沒有設定憑證時整個模組安靜地停用，本機開發照舊用 SQLite。
"""

import json
import os
import threading

_PROJECT_DEFAULT = "my-agent-backend-b8f6f"
_COLLECTION = "tw_radar_users"
_SCOPE = "https://www.googleapis.com/auth/datastore"

_lock = threading.Lock()
_session = None
_project = None
_disabled = False


def _load_credentials_dict() -> dict:
    """從 Secrets 或環境變數取得 service account 憑證。"""
    raw = os.environ.get("FIRESTORE_CREDENTIALS", "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return {}
    try:
        import streamlit as st
        sa = st.secrets.get("firestore", None)
        if sa:
            return dict(sa)
    except Exception:
        pass
    return {}


def _get_session():
    """建立帶 service account 的 HTTP session；失敗就永久停用（不重試拖慢每次 rerun）。"""
    global _session, _project, _disabled
    if _disabled:
        return None, None
    if _session is not None:
        return _session, _project

    with _lock:
        if _session is not None:
            return _session, _project
        info = _load_credentials_dict()
        if not info.get("private_key"):
            _disabled = True
            return None, None
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
            # secrets.toml 的多行字串會把換行寫成 \n 字面值
            info = dict(info)
            info["private_key"] = info["private_key"].replace("\\n", "\n")
            creds = service_account.Credentials.from_service_account_info(info, scopes=[_SCOPE])
            _session = AuthorizedSession(creds)
            _project = info.get("project_id", _PROJECT_DEFAULT)
            return _session, _project
        except Exception:
            _disabled = True
            return None, None


def is_enabled() -> bool:
    return _get_session()[0] is not None


def _doc_url(project: str, user: str) -> str:
    return (f"https://firestore.googleapis.com/v1/projects/{project}"
            f"/databases/(default)/documents/{_COLLECTION}/{user}")


def load_state(user: str) -> dict:
    """從 Firestore 讀回該使用者的完整狀態；沒有或失敗回 {}。"""
    session, project = _get_session()
    if session is None:
        return {}
    try:
        r = session.get(_doc_url(project, user), timeout=15)
        if r.status_code != 200:
            return {}
        fields = r.json().get("fields", {})
        payload = fields.get("state_json", {}).get("stringValue", "")
        return json.loads(payload) if payload else {}
    except Exception:
        return {}


def save_state(user: str, state: dict) -> bool:
    """把該使用者的完整狀態寫回 Firestore。"""
    session, project = _get_session()
    if session is None:
        return False
    try:
        body = {"fields": {
            "state_json": {"stringValue": json.dumps(state, ensure_ascii=False, default=str)},
            "updated_at": {"stringValue": __import__("datetime").datetime.now().isoformat()},
        }}
        r = session.patch(_doc_url(project, user), json=body, timeout=15)
        return r.status_code == 200
    except Exception:
        return False
