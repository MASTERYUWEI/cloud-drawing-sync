# 雲端圖資同步工具（Google Drive 自動同步）

此程式會自動監控指定的 Google Drive 資料夾，當檔案有新增或更新時，自動下載到本地資料夾，並記錄更新日誌。

---

## 🚀 安裝（給使用者 / 工地工程師）

1. 到本專案的 [Releases](../../releases/latest) 頁面，下載最新的 `CloudDrawingSync-x.y.z.msi`
2. 點兩下安裝（**不需要系統管理員權限**，裝到使用者自己的帳號下）
3. 桌面與開始功能表會出現「雲端圖資同步工具」捷徑
4. 第一次開啟：按「登入 Google」完成授權 → 設定下載資料夾 → 按「立即同步」

- 程式**每次啟動會自動檢查新版本**，偵測到新版會詢問是否更新，按「是」即自動下載、安裝、重啟，設定與登入都會保留。
- 設定 / 登入 / 同步狀態存放於 `%APPDATA%\雲端圖資同步工具`，升級與解除安裝都不會動到。

## 📦 發版 SOP（維護者）

1. 改 `drive_sync_gui.py` 開頭的 `APP_VERSION = 'x.y.z'`（版本號唯一來源）
2. 執行 `powershell -ExecutionPolicy Bypass -File installer\build_msi.ps1`
3. `gh release create vx.y.z "dist\CloudDrawingSync-x.y.z.msi" --title "vx.y.z" --notes "更新內容"`
4. 完成——所有安裝戶下次開程式就會收到更新提示

> ⚠️ `credentials.json`（OAuth 憑證）、`token.json` 等機密檔已在 `.gitignore` 排除，**建置 MSI 時需在本機專案目錄放置 `credentials.json`**（見下方 Google Cloud 設定教學）。

## 功能特色

- ✅ 自動監控 Google Drive 資料夾
- ✅ 偵測新增/更新的檔案並自動下載
- ✅ 每 1 小時檢查一次更新
- ✅ 將更新記錄寫入 `update_log.txt`

### GUI 版本（drive_sync_gui.py / 雲端圖資同步工具_新版.exe）

- ✅ 「**今日更新**」摘要可一鍵複製貼到 LINE，**關閉程式後重開仍會保留當天內容**
- ✅ 「**未同步檔案**」頁籤：列出覆蓋失敗的檔案、原因與時間，按「🔁 重試全部」只重新下載失敗的檔案；確定不要追蹤的項目可「移除選取」
- ✅ 「**更新紀錄**」頁籤：依日期展開的歷史紀錄，可查看幾月幾號新增/更新/失敗各幾個檔案、每次同步的明細
- ✅ 以上紀錄存於程式旁的 `sync_history.json`（自動產生，最多保留最近 400 次同步）

---

## 第一步：設定 Google Cloud Console

### 1.1 建立 Google Cloud 專案

1. 開啟瀏覽器，前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 使用您的 Google 帳號登入
3. 點擊上方的專案選擇器（可能顯示「選取專案」）
4. 點擊「**新增專案**」
5. 輸入專案名稱，例如：`DriveSync`
6. 點擊「**建立**」
7. 等待專案建立完成，然後選擇該專案

### 1.2 啟用 Google Drive API

1. 在左側選單，點擊「**API 和服務**」>「**程式庫**」
2. 在搜尋框輸入 `Google Drive API`
3. 點擊搜尋結果中的「**Google Drive API**」
4. 點擊「**啟用**」按鈕
5. 等待 API 啟用完成

### 1.3 設定 OAuth 同意畫面

1. 在左側選單，點擊「**API 和服務**」>「**OAuth 同意畫面**」
2. 選擇「**外部**」，然後點擊「**建立**」
3. 填寫必要資訊：
   - **應用程式名稱**：DriveSync（或您喜歡的名稱）
   - **使用者支援電子郵件**：選擇您的信箱
   - **開發人員聯絡資訊**：填入您的 Email
4. 點擊「**儲存並繼續**」
5. 在「範圍」頁面，直接點擊「**儲存並繼續**」
6. 在「測試使用者」頁面，點擊「**新增使用者**」，輸入您的 Google 帳號 Email
7. 點擊「**儲存並繼續**」
8. 點擊「**返回資訊主頁**」

### 1.4 建立 OAuth 2.0 憑證

1. 在左側選單，點擊「**API 和服務**」>「**憑證**」
2. 點擊上方的「**+ 建立憑證**」
3. 選擇「**OAuth 用戶端 ID**」
4. 應用程式類型選擇「**電腦版應用程式**」
5. 名稱填入：`DriveSync Desktop`
6. 點擊「**建立**」
7. 在彈出視窗中，點擊「**下載 JSON**」
8. **重要**：將下載的檔案重新命名為 `credentials.json`
9. 將 `credentials.json` 放到 `C:\Users\USER\Desktop\drive\` 資料夾中

---

## 第二步：安裝 Python 套件

開啟命令提示字元（CMD）或 PowerShell，在專案目錄執行：

```bash
pip install -r requirements.txt
```

---

## 第三步：執行程式（開發模式）

```bash
python drive_sync_gui.py
```

### 首次執行

1. 按「登入 Google」，程式會自動開啟瀏覽器
2. 使用您的 Google 帳號登入
3. 點擊「**繼續**」（可能會顯示「此應用程式未經驗證」，點擊「進階」>「繼續前往」）
4. 授予「查看您 Google 雲端硬碟中的檔案」權限
5. 設定資料夾 ID 與下載路徑後按「立即同步」

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| `drive_sync_gui.py` | 主程式（GUI，同發佈的 exe/MSI） |
| `flatten_xref_once.py` | 一次性執行「套圖連結整理」的輔助腳本 |
| `installer/Package.wxs` | WiX MSI 安裝包定義 |
| `installer/build_msi.ps1` | 一鍵建置 MSI 腳本 |
| `requirements.txt` | Python 套件依賴 |
| `credentials.json` | Google OAuth 憑證（自行放置，不在 repo 內） |

使用者資料（自動產生，存於 `%APPDATA%\雲端圖資同步工具`）：
`config.json`（設定）、`token.json`（登入權杖）、`sync_state.json`（同步狀態）、
`sync_history.json`（歷史紀錄）、`xref_flat_manifest.json`（套圖連結清單）

---

## 更新日誌

所有檔案更新都會記錄在下載資料夾內的 `update_log.txt`：
```
<你的下載資料夾>\update_log.txt
```

日誌格式範例：
```
[2025-12-30 08:30:00] 程式啟動
[2025-12-30 08:30:05] 新檔案: document.pdf
[2025-12-30 08:30:10] 檔案更新: image.jpg
[2025-12-30 08:30:15] 同步完成 - 新增: 1 個, 更新: 1 個
```

---

## 常見問題

### Q: 如何監控其他資料夾？
在程式的「資料夾 ID」欄位貼上新的 Google Drive 資料夾連結或 ID 後按「立即同步」。
（資料夾 ID 可從 URL 取得：`https://drive.google.com/drive/folders/【這裡就是ID】`）

### Q: 從舊版（免安裝 exe）換到 MSI 安裝版，設定會保留嗎？
會。新版程式啟動時會自動把舊版放在 exe 旁邊的設定檔搬到 `%APPDATA%\雲端圖資同步工具`。
若你的舊 exe 放在別的資料夾且設定沒被帶過來，手動把舊 exe 旁的
`config.json`、`token.json`、`sync_state.json`、`sync_history.json`、`xref_flat_manifest.json`
複製到 `%APPDATA%\雲端圖資同步工具` 即可。
