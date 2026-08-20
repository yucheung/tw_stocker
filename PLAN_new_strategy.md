# MR20 順勢回檔策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個只用日線 OHLCV、每日收盤後即可產生候選單，且能直接交給 `independent_sim.py` 執行的簡單順勢回檔策略。

**Architecture:** MR20 不追逐橫截面最強動量，而是在流動性股票中尋找「中期多頭、短線超賣、已開始止跌」的個股。策略端只負責在收盤後篩選、排序並輸出既有 orders JSON；成交、部位、+4 ATR / -3 ATR 與 20 日 TIME rule 全部沿用 `independent_sim.py`。

**Tech Stack:** Python、pandas、yfinance 日線 OHLCV、XTAI 交易日曆、`independent_sim.py` schema v1。

## Global Constraints

- 核心選股條件固定為 5 條，不增加財報、籌碼、產業分類或機器學習模型。
- 所有指標只使用訊號日收盤時已知資料，不可使用次日價格或向前填補的成交量。
- TP/SL 固定為成交價 `+4 × ATR20` / `-3 × ATR20`。
- TIME rule 固定為進場後第 20 個有效交易日收盤出場。
- 委託採既有 `signal_close_limit_next_open_v1`：訊號日收盤價為次一交易日買進限價，開盤價高於限價即不成交並於 09:30 撤單。
- 每日最多輸出 7 名候選；`independent_sim.py` 自動挑前 2 名，已持有或已有待成交單的股票不重複建立部位。
- 本文件定義可執行規格，不宣稱策略已有正報酬；正式投入資金前仍須回測與紙上交易驗證。

---

## 1. 策略名稱

**MR20 順勢回檔策略（Trend Pullback Mean Reversion）**

- `MR`：Mean Reversion，捕捉短線價格回到均值的反彈。
- `20`：以 20 日均線作為短線回歸目標，也對應 20 個交易日的最長持有期。
- 建議策略識別碼：`mr20_pullback_v1`。

## 2. 選股邏輯

每日收盤後，在所有資料至少有 60 個有效交易日的股票中，依序套用以下 **5 條**：

1. **流動性：** 過去 20 日平均成交額位於全市場前 50 名。成交額定義為 `Close × Volume`。
2. **中期多頭：** `MA20 > MA60`，且 `Close > MA60`。只接多頭架構中的回檔，不接明確空頭。
3. **已回檔：** `Close < MA20`。價格必須已落到短期均線下方，避免和 Top-7 一樣追逐強勢高檔股。
4. **短線超賣：** `RSI(5) <= 35`。RSI 使用 Wilder 算法；資料不足 6 根有效收盤價即不入選。
5. **止跌確認：** `Close[t] > Close[t-1]`。等到收盤已出現第一天反彈，降低直接接 falling knife 的機率。

### 排名規則

條件通過後不做多因子加權，維持單純且可解釋：

```text
score = 100 - RSI(5)
```

- `score` 由高到低排序，也就是 RSI 越低者優先。
- RSI 相同時，20 日平均成交額較高者優先；仍相同則股票代號由小到大。
- 每日輸出前 7 名；不足 7 名就只輸出實際通過者，零檔時不勉強下單。

## 3. 進出場規則

### 3.1 進場

1. 訊號日收盤後計算上述條件並建立最多 7 筆買單。
2. `reference_close` 與 `limit_price` 均設為訊號日收盤價。
3. `execution_date` 必須是訊號日後的下一個 XTAI 交易日。
4. 次一交易日開盤：
   - `0 < Open <= limit_price`：以 Open 全數成交。
   - `Open > limit_price`：不追價，09:30 撤單。
   - Open 缺漏或無效：撤單，不以其他價格替代。
5. `independent_sim.py` 依 rank 只挑最多 2 支；每支使用既有 45% 目標部位與 10% 現金保留設定。

這個限價模型很適合 MR20：策略要買回檔，不應在隔日大幅跳空反彈後追價。

### 3.2 ATR 定義

為了和 v8.5 及 `independent_sim.py` 完全一致，這裡的 ATR20 沿用現有 close-based 定義，而不是傳統 True Range ATR：

```text
ATR20 = mean(abs(Close.pct_change()), 20) × signal_date Close
```

ATR 在建立委託時固定，成交後不移動、不重新計算。

### 3.3 出場

成交價記為 `fill_price`，建立部位時一次設定：

```text
TP = fill_price + 4 × ATR20
SL = fill_price - 3 × ATR20
TIME = 進場後第 20 個有效交易日收盤
```

- 每日判斷優先序沿用模擬器：`SL > TP > TIME`。
- 當日 Low 觸及 SL 時以 SL 出場；若開盤已跳空低於 SL，使用較差的開盤價。
- 未觸及 SL 且當日 High 觸及 TP 時以 TP 出場；若開盤已跳空高於 TP，使用較佳的開盤價。
- 兩者皆未觸發且 `day_count >= 20`，以當日收盤價 TIME 出場。
- 不加移動停損、損益兩平、加碼或提早出場，避免策略複雜化。

## 4. 與現有 Top-7 v8.5 的差異

| 面向 | Top-7 v8.5 | MR20 順勢回檔 |
|---|---|---|
| 核心邏輯 | `20 日動量 rank × 3 + MA60 乖離 rank × 1` | 多頭趨勢內的短線超賣與止跌 |
| 買進型態 | 買相對強、偏離均線較高的領先股 | 買仍在多頭架構、但跌破 MA20 的回檔股 |
| 排名方法 | 多因子橫截面加權 | 單一 `100 - RSI(5)` 排序 |
| 預期市場優勢 | 趨勢延續、強者恆強 | 過度賣壓後回到短期均值 |
| 常見失效環境 | 動量快速反轉、領先股補跌 | 空頭初期、回檔演變成趨勢反轉 |
| 與 Top-7 重疊 | 容易位於 20 日漲幅前段 | 因 `Close < MA20` 與短 RSI 超賣，通常不會是當日強動量前段 |

兩套策略共用流動性池與執行風控，是為了避免交易不可行；alpha 來源則分別是「趨勢延續」與「多頭中的短線均值回歸」。因此 MR20 可作為 Top-7 的互補策略，而不是只換一組權重。

## 5. 適合的標的特性

最適合：

- 成交活躍、開盤價格可靠、滑價相對低的上市櫃大型或中大型股。
- MA20 仍在 MA60 上方，基本趨勢未破壞，但近期因市場震盪或獲利了結而快速回檔。
- 日內有足夠波動，能在 20 個交易日內觸及 +4 ATR，同時不是經常跳空鎖死的股票。
- 波動中等、價格會圍繞短期均線來回，而非長期單邊直線上漲的股票。

不適合：

- 成交稀疏、長時間停牌、容易無量漲跌停或開盤價失真的股票。
- MA20 已跌破 MA60、Close 已跌破 MA60 的明確空頭股。
- 事件風險極高、連續跳空，或短線超賣可能代表基本面永久惡化的個股。
- 強勢突破後一路沿 MA20 上漲、幾乎不回檔的純動量股；這類標的應由 Top-7 處理。

## 6. `independent_sim.py` 相容方式

不必修改 `independent_sim.py`。MR20 只需將候選寫成相同的 `{"orders": [...]}` artifact；檔名可獨立使用 `artifacts/orders_mr20_YYYYMMDD.json`，並以 `--orders` 明確傳入。

每筆 order 至少提供下列欄位。以下是 schema 範例，價格與指標值僅示範資料格式：

```json
{
  "orders": [
    {
      "signal_date": "2026-08-19",
      "execution_date": "2026-08-20",
      "ticker": "2330",
      "side": "buy",
      "rank": 1,
      "score": 72.0,
      "reference_close": 1180.0,
      "limit_price": 1180.0,
      "atr": 25.0,
      "tp_atr_mult": 4.0,
      "sl_atr_mult": 3.0,
      "max_hold_days": 20,
      "order_type": "limit",
      "entry_model": "signal_close_limit_next_open_v1",
      "time_in_force": "DAY_UNTIL_0930",
      "model_version": "mr20_pullback_v1"
    }
  ]
}
```

使用獨立資料目錄，避免和既有 Top-7 模擬狀態混在一起：

```bash
python independent_sim.py init \
  --data-dir independent_sim_data_mr20 \
  --capital 200000

python independent_sim.py close-and-plan \
  --data-dir independent_sim_data_mr20 \
  --orders artifacts/orders_mr20_20260819.json \
  --as-of 2026-08-19

python independent_sim.py open \
  --data-dir independent_sim_data_mr20 \
  --as-of 2026-08-20

python independent_sim.py status --data-dir independent_sim_data_mr20
python independent_sim.py report --data-dir independent_sim_data_mr20
```

## 7. 每日操作流程

```text
交易日收盤後
  → 更新日線 OHLCV
  → 套用 5 條 MR20 條件
  → 依 100 - RSI(5) 排名並輸出最多 7 筆 orders
  → 執行 close-and-plan（先結算舊部位，再建立次日限價單）

次一交易日開盤後
  → 執行 open
  → 開盤不高於訊號日收盤才成交，否則撤單

每日重複
  → SL / TP / TIME 由 independent_sim.py 管理
```

## 8. 上線前最小驗收

- [ ] 任選一個歷史訊號日，以手算確認 5 條條件、RSI(5)、rank 與 score。
- [ ] 驗證 orders 的所有 `execution_date` 都是 `signal_date` 的下一個 XTAI session。
- [ ] 驗證 `limit_price == reference_close == signal_date Close`。
- [ ] 驗證每筆 order 都帶有 `tp_atr_mult=4.0`、`sl_atr_mult=3.0`、`max_hold_days=20`。
- [ ] 用獨立 `--data-dir` 跑一次 `close-and-plan`、`open`、`status`，確認不讀寫 Top-7 的既有模擬狀態。
- [ ] 至少完成涵蓋多頭、盤整、空頭區間的走勢回放，再決定是否投入真實資金。

## 9. 明確停止條件

MR20 v1 到此為止。首版不加入市場大盤濾網、基本面、法人籌碼、產業中性、動態 RSI 門檻、移動停損或 ML；只有在獨立回測證據指出具體失效原因時，才另開版本評估一項變更。
