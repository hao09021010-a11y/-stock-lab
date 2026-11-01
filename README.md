# 我的選股實驗室

手機版個人選股網站。每個交易日收盤後，GitHub Actions 會自動抓證交所（上市）資料、算指標、更新 `data/screen.json`，網頁直接讀這個檔案。

## 架設（約 10 分鐘）
1. 在 GitHub 建一個新的 repository，把這個資料夾的所有檔案上傳（包含隱藏的 `.github` 資料夾）。
2. 到 Settings → Pages，Source 選 `Deploy from a branch`，Branch 選 `main`、資料夾選 `/ (root)`。
3. 到 Actions 頁面，選 `update-data` → `Run workflow`，手動跑第一次（會回補約 30 個交易日，需要幾分鐘）。
4. 跑完後，用 `https://<你的帳號>.github.io/<repo 名稱>/` 開啟。iPhone 可用「加入主畫面」。

之後每個交易日 15:30（台灣時間）會自動更新。

## 說明
- 目前只有上市股票，資料為收盤後，非即時。
- 沒有外資資料或讀不到 `screen.json` 時，外資會顯示 0，或頁面改顯示內建示範數字。
- 支撐、壓力、評分的算法在 `scripts/build_data.py` 的 `build_screen()`，可依自己的想法修改。找不到支撐或壓力時，以 ±5%、±10% 代替。
