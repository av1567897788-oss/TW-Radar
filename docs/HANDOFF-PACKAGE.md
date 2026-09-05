# AI Agent 辦公室 — 交接封包

> **給另一個 Claude session 讀的自足封包。** 讀完這份就能接手，不需要原始對話。
> 最後更新：2026-09-03
> 權威檔案在 `~/Desktop/Code/agent-backend/`，本檔是收斂版索引。

---

## 一、這是什麼

老闆（楊沛駩，職業軍人）要建一個三員工 AI 辦公室，**人在部隊也能用手機下指令、收成果**。

```
老闆（手機）
  ↓ 下指令
Codex ──判斷──┬─ 簡單 → 直接回覆老闆（省 token）
              └─ 複雜 → 派工
                        ├─ Claude Code（安全把關／複審／影片全包）
                        └─ Gemini（文書／表格／批次整理）
                              ↓
                    Firestore 共用控制面
                              ↓
                    Codex 彙整 → 回報老闆（Artifact／Google Drive）
```

**「上雲」的真正意義**：三個 agent 讀**同一份知識庫與 skill**，不是把排程搬上雲。

---

## 二、三位員工

| | 角色 | 職責 | 即時對話 |
|---|---|---|---|
| **Codex** | **單一指令入口** | 手機／語音接指令、判簡單複雜、派工、彙整交付 | ✅ |
| **Claude Code** | 技術總監 | 安全把關、品質複審、複雜任務、**影片全包** | ❌ |
| **Gemini** | 基層助理 | 文書、表格、批次整理、**NotebookLM 聯動** | ❌ 未安裝 |

- 簡單任務 **Codex 直接回**，不喚醒其他人
- **複審只在 M-01~04／Q-01~05 觸發**，不是每一步
- Gemini 保留的真正理由是 **NotebookLM**（分析強、可產短片與 PPT）

---

## 三、互相呼叫（雙向已打通，實測過）

### Codex → Claude Code
```bash
claude -p '任務內容' --model claude-sonnet-5 --permission-mode bypassPermissions
```
實測 **5.1～7.3 秒**。用 `--no-session-persistence`，每次全新 session、不累積 context。

### Claude Code → Codex
```bash
~/Desktop/Code/agent-backend/bin/wake-codex.sh '任務'
```
CLI 在 `/Applications/Codex.app/Contents/Resources/codex`
- **模式 A**（`codex exec`）：11.5 秒，答案回 Claude
- **模式 B**（`codex queue`，加 `--to-session`）：塞進老闆當前對話，**手機看得到**

### 認證（已結案）
`claude auth login` 已完成（根因是 CLI 端從未登入，不是過期）。
**refreshToken 自動續期已驗證成立** → 不需要 `claude setup-token`。
未驗證：Mac 長睡後第一次呼叫、多天無人操作是否持續續期。

---

## 四、常設授權（老闆定案，權威在 `shared-agent-workspace/AGENT_DISPATCH_PROTOCOL.md` §13）

```yaml
authorization:
  routine_in_scope:                   standing_approved
  paid_or_financial:                  user_confirmation_required   # 紅線一
  sensitive_or_personal_data_outflow: user_confirmation_required   # 紅線二
  platform_forced_interaction:        report_to_codex
  destructive_out_of_scope:           user_confirmation_required
```

**老闆原話**：「除了這兩條紅線，其餘授權我都同意，你們甚至要用我的終端機都可以。」

**可直接做，不用問**：檔案讀寫、程式執行、測試、終端機、瀏覽器、MCP、免費工具安裝、暫存清理、Agent Message 讀寫、代理喚醒。

**遇紅線時**：不等老闆看畫面，直接吐給 Codex——
```
APPROVAL_REQUIRED::{"category":"payment|personal_data_outflow|destructive_out_of_scope|platform_interaction","action":"...","target":"...","reason":"...","cost":"...","data_scope":"...","reversible":true|false,"resume_step":"..."}
```
Codex 四選一：`AUTO_APPROVED` / `USER_APPROVAL_REQUIRED` / `DENIED` / `PLATFORM_INTERACTION_REQUIRED`
等待期間**先存 checkpoint**，同意後從 `resume_step` 續跑，不重做。

### ⚠️ 待老闆最終裁決：個資規則的修正提案

**現行**：`.env`、Keychain、cookie、`~/.ssh/` 一律不可放行。
**問題**：`.env` 裡是 `EVOLINK_API_KEY`，影片生圖本來就要讀 → 會卡住任務，違反老闆「不能影響執行權限」。

**提案（讀取自用 vs 外流拆開）**：
| 動作 | 判定 |
|---|---|
| 讀 `.env` 取 key **在本機跑任務** | ✅ 放行 |
| key／cookie／個資 **寫進訊息、repo、雲端、給第三方** | 🔴 紅線 |
| 讀 `~/.ssh/`、Keychain、瀏覽器 Login Data | 🔴 維持禁止 |

已送 Codex（`M-20260902-1030-cc-23`），**尚未定案**。

---

## 五、🔴 出網閘門（D-04 定案，即刻生效）

| 動作 | 規則 |
|---|---|
| 讀寫本機檔案、本機計算、內部處理 | ✅ 直接做 |
| **把老闆的資料送到網路上任何地方** | 🔴 **先問，等同意才動** |

含：推 GitHub、上傳雲端硬碟、貼進第三方 API／網站、寄 email 附件、發佈公開網頁。

**確認框必須跳在 Codex 那端**（老闆在手機時看的是 Codex）：
```bash
./bin/wake-codex.sh --to-session '【核准請求】…'
```
送出後**停下來等**。不預先執行、不「先做了再回報」。

**已豁免（D-06）**：老闆說「任務完畢叫你們上傳就直接傳」→ **Artifact／Google Drive 不用再問**。

---

## 六、📱 交付規則（強制，權威在 `~/.claude/skills/dispatch-remote/DELIVERY-RULES.md`）

| 情況 | 去處 |
|---|---|
| 老闆明講「做在桌面」 | 照做，不自作主張 |
| 報告、分析、清單 | **Artifact URL**（手機直接看） |
| Word／PPT／Excel／PDF／影片 | **Google Drive** |

**鐵則：回報一定要寫「東西放這裡」＋可點連結。只說「做好了」＝沒做。**
待裁決事項要**編號**並寫明怎麼回（例：「學 2、5、7」）。

---

## 七、🎬 影片分工（寫死，不得變更）

① 史料查證＋參考資料搜羅 → **Claude Code**
② 生成圖片 → **Codex**（ChatGPT 內建 Image-2 免費額度），額度用完可退回 EvoLink
③ 判圖（場景／服裝／角色 OK 嗎）→ **老闆本人**
④ 影片製作 → **Claude Code**，EvoLink Seedance 2.5 ＋ 既有 skill ＋ 十道閘門

**老闆現場教的修正必須寫進 skill**，不能只留對話。

**參考圖來源分級**：S 出土文物／A 考據款公仔／B 學術復原圖／C 商業創作甲與影視劇照。
主體形制只能用 S 或 A。**先查本機 `來源.md` 再判級，不得目視推翻。**

---

## 八、💰 Token 控管（實測數據）

09-01～09-02 共 34 個 session 的實際用量：

| 來源 | cache 讀取 | output |
|---|---|---|
| **單一互動式長對話**（569 則訊息） | **270,144,404** | **979,220** |
| 其餘 33 個 session 加總 | 41,605,563 | 227,651 |

**單一長對話吃掉 86% cache 讀取、81% output。**
headless 每次只有 3～8 則訊息，cache 讀取 9～60 萬 —— **便宜到可忽略**。

### 三條規則

1. **新任務一律開新 session**。判準：**需不需要前一任務的產物或決策脈絡**？不需要就開新的。
2. **headless 不需要 compaction**（`--no-session-persistence` 不累積）。
3. **互動式長對話不靠 `/compact`，靠換場** —— 寫保真交接包 → 開新 session 接續。比壓縮可靠且更省。

⚠️ `/compact` 無法可靠自動觸發，**不得把文字命令誤報成已壓縮**。

---

## 九、Firestore 控制面

專案 `my-agent-backend-b8f6f`，Firestore `(default)` @ `asia-east1`
五張表：`agent_registry`／`agent_tasks`／`agent_handoffs`／`agent_events`／`agent_messages`
規則全禁用戶端直連，agent 走 Admin。

**⚠️ 校園網路坑**：`developerknowledge.googleapis.com` 被擋 → **Firebase MCP 整個掛掉**。
`firestore.googleapis.com` 本身是通的 → **改走 REST API**：
```bash
# 用 ~/.config/configstore/firebase-tools.json 的 refresh_token 換 access_token
# 再打 https://firestore.googleapis.com/v1/projects/.../documents/agent_messages
```

---

## 十、🧠 共享記憶（設計已定，**尚未實作**）

**現況是三個孤島**：我的在 `~/.claude/projects/.../memory/`、Codex 在 `~/.codex/memories`、Gemini 未裝。**三方共享目前等於零。**

**設計**：記憶放 **`shared-knowledge` repo**，不放 Firestore。
- repo 可 grep、有版本控制、三方都有 `gh`、純文字很小
- Firestore 是**控制面**，塞記憶會變第二個真相來源

```
shared-knowledge/memory/
├── INDEX.md          ← 一行一則，含關鍵字與指向
└── xxx.md            ← 一則一檔，frontmatter 含 keywords
```
**檢索靠 grep 關鍵字，不是全部載入。** 任務完成時誰做的誰寫。

⚠️ **前提：`shared-knowledge` repo 還沒建。在那之前不得宣稱三方共享已完成。**

---

## 十一、無人值守電源設定（已完成，老闆本人執行）

```bash
sudo pmset -c disablesleep 1                     # 系統不睡
sudo pmset -c displaysleep 5 -b displaysleep 2   # 螢幕會關（關鍵）
```

**⚠️ 只跑第一行會開安全洞**：macOS 螢幕鎖定跟著「螢幕關閉」觸發。
`displaysleep 0` → 鎖定永不啟動 → **任何人掀蓋就是解鎖桌面**。
**下次設無人值守機器要一起檢查這兩項。**

實測：`SleepDisabled=1`｜螢幕關後 **5 秒**要密碼｜FileVault On｜Touch ID 啟用。
`-c` 只影響接電源，**電池照常休眠**（放包包安全）。

**實測證據**：`Entering Sleep state due to 'Clamshell Sleep' : Using AC` —— 闔蓋接電**原本照樣睡**，DarkWake 只有 5～32 秒，跑不動任務。

---

## 十二、排程現況（11 條）

由 Claude Code 負責，每天回報：
`daily-gmail-triage` 08:02｜`check-pending-followups` 09:00｜`tw-radar-daily-health-check` 15:02（週一至五）｜`daily-obsidian-organize` 17:07｜`daily-fb-saved-triage` 20:00｜`daily-desktop-cleanup` 20:07｜`gmail-weekly-organizer` 週一｜`weekly-obsidian-organize` 週日｜`weekly-security-maintenance` 週日

**新增／變更**：
- **`daily-delivery-digest`（21:33）** — 當天所有排程彙整成**一份 Artifact**（手機看）＋存 Google Drive，一天一個連結不洗版，含待裁決編號清單
- **`agent-inbox-poll`（每 2 小時）** — **已降為只讀＋通知**，不回覆、不標已讀
  - 原因：2026-08-30 13:22 它曾**無人值守自動寫了一整份架構藍圖給 Codex**（`M-20260830-1320-cc-08`），老闆與 Claude Code 本人都沒看過就送出
  - **恢復需老闆核准，排程不得自行恢復**

**通則**：無人值守的自動回覆只適合「錯了代價低」的例行往返；**決策型、會產生約束力的訊息不適用。**

---

## 十三、老闆 2026-09-02 的裁決

| 編號 | 結果 |
|---|---|
| **D-01** | Gemini **由 Codex 安裝**。老闆願配合做一次 Google 登入 |
| **D-02** | 「依你判斷的做」→ `REPO_SCOPE.md`／`LOCAL-ONLY.md`／`EXTERNAL-DEPS.md`／`bin/pack-shared.sh` 已建，**staging 就緒，尚未建 repo、尚未推送** |
| **D-03** | ✅ 電源與安全設定完成（見第十一節） |
| **D-04** | 出網閘門（見第五節） |
| **D-05** | ✅ 已送出 |
| **D-06** | ✅ Artifact／Google Drive 上傳**豁免確認** |

### 📚 共享知識庫的真正理由

**今天教誰的東西，都要沉澱成 skill 或知識庫，三個 agent 都讀得到。**
老闆教 Claude Code 的 → Codex 和 Gemini 也學得到；反之亦然。
**目標是同步認知，不是各自累積。**

---

## 十四、Claude Code 認過的錯（避免重犯）

1. 「零憑證」→ 錯，GitHub Actions 一定有 `GITHUB_TOKEN`
2. `contents:write` 目錄級限制 → **不存在**，GitHub 權限是 repo 層級
3. skill 只推 `.md` → 會產生殘缺 skill（43 個必要腳本）
4. 提議 JSONL fact store → **自我矛盾**（反對 BEADS 的理由適用於自己）
5. `expired` 欄位存在 → 不等於 metadata 永久保存
6. 把 `玄甲軍_實物考據_六視圖.jpg` 目視判 C 級 → **沒查自己參考庫的 `來源.md`**
7. 說「叫不動 Codex」→ **搜錯路徑**，CLI 在 `/Applications/Codex.app/Contents/Resources/`
8. 說「TCC 權限被撤銷」→ 其實只是**權限狀態沒重新載入**

**共同模式：驗證到機制存在，就當成保證。**
**紀律：驗證到什麼只能寫什麼，不得外推。**

---

## 十五、下一步

1. ~~認證驗證~~ ✅ 結案
2. ~~雙向呼叫協定~~ ✅ `CODEX_CALLS_CC.md`
   - 剩：模式 B 沒驗手機端顯示樣貌、非同步長任務沒跑端到端
3. **接 Gemini** — 由 Codex 負責，等他回報
4. **兩個 private repo 落地** — staging 就緒，尚未推送
5. **共享記憶實作** — 等 repo 建好
6. **個資規則修正案定案** — 等老闆裁決（見第四節）

---

## 十六、權威檔案索引

| 檔案 | 內容 |
|---|---|
| `agent-backend/STATUS.md` | 最新狀態，**開場先讀** |
| `agent-backend/AGENT_OFFICE.md` | 組織結構、路由、影片規格、五張表 |
| `agent-backend/CODEX_CALLS_CC.md` | 雙向呼叫協定 |
| `agent-backend/CLOUD_BLUEPRINT.md` | 雲端藍圖（Tier 0/1、schema、權限矩陣） |
| `agent-backend/CROSS_REVIEW_CC.md` | 與 Codex 的逐點交叉審查 |
| `agent-backend/KNOWLEDGE_INDEX.md` | 知識索引 |
| `agent-backend/REPO_SCOPE.md` | repo 納入範圍 |
| `shared-agent-workspace/AGENT_DISPATCH_PROTOCOL.md` | **派工協議（授權邊界 §13、compaction §14）** |
| `~/.claude/skills/dispatch-remote/DELIVERY-RULES.md` | 交付規則 |

---

## 十七、接手者請注意

1. **不要重開已定案的事**（第十三節的 D-01～D-06、第四節授權邊界）
2. **紅線只有兩條**：付費／金融、個資外流。其餘常設授權
3. **出網要先問**，確認框跳在 Codex 那端
4. **新任務開新 session**，不要延續長對話
5. **驗證到什麼只能寫什麼**，不得外推（第十四節那八個錯都是這樣來的）
6. **`shared-knowledge` repo 還沒建** —— 凡涉及「三方共享」的，現在都還做不到
