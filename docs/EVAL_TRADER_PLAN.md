# tw_stocker 投資評估系統 — 實作計畫

## 核心問題
tw_stocker 的 MR20 和 Top-7 策略長期零交易，無法進行策略評估。

## 方案：eval_trader.py（最小可行評估系統）

### 架構
單檔 Python 腳本（~250行），不依賴 tw_stocker 現有複雜 pipeline。

### 信號產生（解決零交易）
- 動量信號：20日均線之上 + 成交量 > 20日均量1.5倍
- 每天掃描 TWSE 上市股，取 Top-5
- 門檻低，確保有交易產生

### Paper Trade 模擬
- 純本地模擬，不需要 Alpaca
- 進場：信號日收盤價
- 出場：TP +4 ATR / SL -3 ATR / 20日 TIME
- 持倉上限 5 檔

### 評估指標
- Sharpe Ratio（年化）
- Max Drawdown
- Win Rate
- Average Trade Return
- Alpha vs 0050
- Total Return

### 輸出
- eval_report.json：metrics + 每筆交易記錄

### 技術約束
- 單檔，用 yfinance 拉資料
- ponytail 原則：最簡潔、不 over-engineer
- 200-300 行

### 與 TradingAgents 的關係
- 本版不整合 TradingAgents（先建 baseline）
- 評估報告可作為後續整合的比較基準
