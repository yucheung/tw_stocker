# Top-7 部位週轉機制改善 Plan

**Source:** Claude Code (Sonnet) 獨立分析  
**Date:** 2026-08-21  
**Status:** 待用戶確認後執行

---

## 一、Executive Summary

現行 `update_tracker()` 只有「持有到 TP/SL/TIME 到期」單一離場邏輯（`paper_tracker.py:326-340`），完全沒有「新訊號評分 > 強勢持倉 → 主動換股」的機制。當 7 個部位滿倉、且現金低於 `reserve_cash`（`reserve_ratio=0.20`，L294）時，當日所有新訊號會被直接撤單（`CANCELLED_NO_CAPACITY` L440-449 / `CANCELLED_INSUFFICIENT_CASH` L494-503），資金完全鎖死，即使新訊號品質遠優於既有持倉也無法換手。

**docs 分析未指出、但實作前必須解決的缺口：**
1. `positions` dict（L510-518）**沒有存 `rank`/`score` 欄位**，Option A/C 的「score 比較」在目前資料結構下無法比對「新訊號 vs 持倉」，必須先在開倉時把訊號的 `rank` 存入部位。
2. 訊號來源（`extract_signals_from_orders`，L182-243）目前只有**序位 `rank`**（1,2,3…），沒有連續型 score／信心值。docs 裡「`new_score > held_score × 1.1`」這種乘法門檻對「序位」無意義（rank 是名目尺度，不能做比例運算），需要改用「rank 名次差」或改由 upstream（AI report 產出 orders JSON 時）補上真正的連續 score。
3. 目前「訊號數 > 剩餘名額」時，多出的訊號會被直接捨棄（`available_slots` 截斷，L539-545），未被排入「候補置換佇列」，即使做了 turnover 邏輯，這些訊號也不會被納入置換候選。

---

## 二、Problem Diagnosis（含程式碼行號）

| # | 問題 | 位置 | 說明 |
|---|------|------|------|
| D1 | 滿倉時新訊號一律撤單，無置換邏輯 | `paper_tracker.py:440-449` | `if ticker in data['positions'] or len(data['positions']) >= MAX_POSITIONS:` → 直接 `CANCELLED_NO_CAPACITY`，不檢查新訊號是否優於既有最弱部位 |
| D2 | 離場條件只有 SL/TP/TIME，無主動換股離場 | `paper_tracker.py:326-340` | `reason` 只會被設為 `'SL' / 'TP' / 'TIME'` 三者之一；沒有 `'REPLACE'` 分支 |
| D3 | `max_hold = 20` 天過長，資金回收慢 | `paper_tracker.py:293` | docs 統計：TP 出場平均僅需 9.2 天，TIME 出場（20 天到期）平均報酬僅 +1.9%，顯示長天期持倉是低效佔用資金的主因 |
| D4 | 部位物件未保存進場時的訊號強度 | `paper_tracker.py:510-518` | `data['positions'][ticker] = {'entry':..., 'tp':..., 'sl':..., 'entry_date':..., 'shares':..., 'day_count':0, 'max_hold_days':...}` → 缺 `rank`/`score`，導致「持倉最弱者」無從定義 |
| D5 | 超額訊號直接丟棄，不進候補佇列 | `paper_tracker.py:539-545` | `new_pending = [...for s in signals[:available_slots]]`，`available_slots = MAX_POSITIONS - len(positions)`；滿倉時 `available_slots=0`，所有訊號被截斷 |
| D6 | `reserve_cash` 與 `position_size=0.10` 疊加後可用資金過緊 | `paper_tracker.py:294-295, 482-489` | 20% 保留現金 + 每倉 10%×regime_scale，7 倉即可能達 70-100% 曝險 |
| D7 | 訊號評分只有序位 `rank`，非連續分數 | `paper_tracker.py:223` | `'rank': order.get('rank')` 是名次（1,2,3…），乘法比較邏輯不適用於名次尺度 |

---

## 三、Mechanism Comparison Matrix

| 準則 | A: Score-based | B: Time-based | C: Hybrid ⭐ | D: Sizing | E: Dynamic MAX |
|------|------|------|------|------|------|
| 交易頻率 | 3-5/月 | 5-7/月 | 3-5/月 | 2-3/月 | 4-6/月 |
| 資金利用率 | ~80% | ~70% | ~75% | ~40% | ~60% |
| Whipsaw 風險 | 高 | 低 | 中 | 低 | 中 |
| 實作複雜度 | 中 | 低 | 高 | 低 | 高 |
| **現有資料相容性** | ⚠️ 需先補 rank 持久化 | ✅ 一行常數 | ⚠️ 需補 rank + 新分支 | ✅ 一行常數 | ⚠️ 需重寫容量檢查 |
| **實作風險** | 中 | 極低 | 中高 | 低 | 高 |

**結論：** P0 階段應先做 B（時間縮短）+ D（sizing 下修）——不需要資料結構變更、可獨立驗證；C（score-based replace）因需先解決 D4/D7 資料缺口，歸入 P1。

---

## 四、Recommended Implementation

### Step 1（P0）：縮小單倉曝險 + 縮短持有期

```python
# paper_tracker.py:293 附近
max_hold = 15          # 20 → 15，資金加速回收
# L482/485 預設值同步調整
position_size_default = 0.07   # 0.10 → 0.07，7 倉滿倉僅佔 49% + regime_scale 曝險
```

### Step 2（P1）：部位保存 rank，開倉時寫入

```python
# L510-518 開倉邏輯，新增一個欄位
data['positions'][ticker] = {
    'entry': fill_price,
    'tp': tp_new,
    'sl': sl_new,
    'entry_date': today,
    'shares': shares,
    'day_count': 0,
    'max_hold_days': sig.get('max_hold_days', max_hold),
    'entry_rank': sig.get('rank'),        # 新增：記錄進場時的訊號序位
}
```

### Step 3（P1）：滿倉時的置換評估

```python
def find_replace_candidate(positions, new_rank, today):
    """回傳應被置換的 ticker，若無合適對象回傳 None"""
    candidates = []
    for ticker, pos in positions.items():
        held_rank = pos.get('entry_rank')
        day_count = pos.get('day_count', 0)
        if held_rank is None or new_rank is None:
            continue
        rank_gap = held_rank - new_rank
        if day_count >= 10 and rank_gap >= 1:
            candidates.append((ticker, day_count, rank_gap))
        elif day_count >= 5 and rank_gap >= 3:
            candidates.append((ticker, day_count, rank_gap))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[1], -c[2]))
    return candidates[0][0]

# L440-449 改為：
if ticker in data['positions']:
    ... # 撤單邏輯不變（重複持有）
elif len(data['positions']) >= MAX_POSITIONS:
    replace_ticker = find_replace_candidate(data['positions'], sig.get('rank'), today)
    if replace_ticker is None:
        order_events.append({**event_base, 'status': 'CANCELLED_NO_CAPACITY', ...})
        continue
    force_close(data, replace_ticker, bars.get(replace_ticker), today, reason='REPLACE')
    # 繼續往下走正常開倉流程
```

---

## 五、Expected Impact

| 指標 | 現況 | P0 後（sizing+hold） | P0+P1 後（+置換） |
|------|------|------|------|
| 最大停滯天數 | 20 天 | 15 天 | 10-12 天 |
| 月交易頻率 | 0-1 | 1-3 | 3-5 |
| 資金利用率 | ~20% | ~40% | ~55-65% |
| 平均持倉天數 | 14 天 | ~11 天 | ~9-10 天 |

---

## 六、Risk Considerations

1. **Whipsaw**：rank_gap 門檻 + day_count 分層 + 持有最久者優先讓位雙重防呆
2. **交易成本**：一次 REPLACE ≈0.87%，月換手 4 次 ≈3.5% 成本拖累
3. **rank 語意**：需確認 upstream 的 rank 穩定性（每日全市場重新排名）
4. **回測同步**：P0 改動後必須同步 event_backtest.py sizing 常數

---

## 七、P0 / P1 / P2 Roadmap

| Priority | 項目 | 工作量 |
|---|---|---|
| **P0** | `max_hold` 20→15 | 1 行 |
| **P0** | `position_size` fallback 0.10→0.07 | 1 行 |
| **P0** | 觀察 1-2 週資金利用率 | — |
| **P1** | 部位新增 `entry_rank` 欄位 | ~2 行 |
| **P1** | 抽出 `force_close()` 共用平倉邏輯 | ~20 行重構 |
| **P1** | 滿倉置換判斷 `find_replace_candidate` | ~30 行 |
| **P1** | `'REPLACE'` reason 記錄 | ~5 行 |
| **P2** | 日報 turnover 指標 | ~15 行 |
| **P2** | 候補佇列機制 | 中～高 |
| **P2** | 重跑回測驗證新參數 | 中 |

**建議順序：** 先上 P0 兩行改動並觀察 1-2 週 → 若停滯問題仍未解決，再啟動 P1。
