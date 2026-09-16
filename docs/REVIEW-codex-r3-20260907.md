# Codex R3 補修審查 — 2026-09-07

**總判定：PARTIAL，7.5 / 10；未達 ≥8.5 放行門檻，不放行。**

審查 `6fe7f86`（缺單補跑）、`559901a`（監控與 smoke）、`c9db230`（過期 JSON），基準為 `a63681c`，HEAD 為 `c9db230831722af64444603996ed2ecd76086a62`。依 `docs/REVIEW-codex-r2-20260907.md` 驗收以下四缺口；行號指 HEAD。本次只新增本文件，不修改程式碼、測試或既有未提交資料，未建立 commit。

## 1. 四缺口逐項驗收

| 缺口 | 判定 | 分數 /10 | 證據與界線 |
|---|---|---:|---|
| 缺單補跑（r2 F1） | PARTIAL | 7.0 | `independent_sim.py:1640–1661` 允許已處理但仍有 gap 的同日規劃重試；直接跳過結算段。晚到顯式訂單能建立 pending、清除 gap，重跑不增加 equity 列；仍缺檔可再試，顯式缺檔拋錯且保留 gap；但已跑隔日 open 後仍可補入過期 pending 並清 gap，見 F5。 |
| 監控假綠（r2 F1/F2） | PARTIAL | 8.0 | `check_processed_runs.py:48–51` 真正讀取當日 gap；CLI 測試證明兩個 run_id 齊全但有 gap 時 stderr 告警、exit 1。函式層缺陷已修；每日排程接線仍無 repo 證據，runner 的失敗 exit 0 仍在，不能宣稱每日監控已上線。 |
| smoke 產單段（r2 F2） | PASS | 9.0 | `tests/test_e2e_mr20_smoke.py:40–101` 以固定行情呼叫真實 `generate_mr20_orders` 和 `save_orders_to_json`，再經真實 close-and-plan → open，驗證 PENDING/FILLED 與持倉落地。這次確為離線三跳；尚非 CLI、runner、跨主機交付或排程驗收。 |
| 過期 fallback（r2 F3） | PARTIAL | 6.0 | 上層 `extract_signals_from_report` 現在對整份過期 JSON、合法空單停止 HTML fallback，新增測試有效；混合日期仍放行舊單，JSON 讀取失敗仍被當無來源而回退未驗日期 HTML。 |

總分採四項等權平均：`(7.0 + 8.0 + 9.0 + 6.0) / 4 = 7.5`。PASS 僅針對表列缺口，不代表所有 r2 上線條件都已完成。

## 2. 殘留問題與新回歸

### F1 — P1：混合日期仍放行舊單（r2 已知、未修）

`paper_tracker.py:208–211` 只要求日期集合包含 today；`:214–258` 沒有逐單拒絕。因此同檔一筆今日、一筆 2020 年訊號，兩筆都從真正上層入口輸出。缺日期訂單亦沒有在此被拒絕。這不是本輪新引入，但直接屬於 r2 F3，不能將 freshness 完整性判 PASS。

本次臨時目錄重現（兩筆皆有效 buy／limit／TP／SL）：

```text
MIXED_DATES [('2330', '2026-09-07'), ('2059', '2020-01-01')]
exit 0（成功重現漏洞）
```

建議逐單驗證來源日期，對缺日期建立明確相容性契約，加入上層混合日期驗收。

### F2 — P1：JSON 損壞仍繞入未驗日期 HTML（既有行為殘留）

`paper_tracker.py:198–203` 捕捉讀取／解析失敗後回傳 `no_source`，與「完全缺檔才回退」契約不符。`:275–284` 隨即讀取未驗日期的 HTML。檔案存在但截斷／格式錯誤時，仍可能生成舊訊號。

本次將唯一 JSON 寫為 `{broken`，並放入一列可解析 HTML，呼叫真正上層入口：

```text
⚠️ orders JSON 讀取失敗: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
BROKEN_JSON_FALLBACK [{'ticker': '2059', 'entry': 100.0, 'tp': 120.0, 'sl': 90.0, 'max_hold_days': 20}]
exit 0（成功重現漏洞）
```

建議「來源存在但不可讀」回傳拒絕狀態；若維持缺檔 HTML 入口，也須驗證 HTML 來源日期。不要將本次保留的 fallback 行為誤列為新回歸。

### F3 — P2：合法零筆補跑印出錯誤的未解 gap 訊息（新增）

`independent_sim.py:1603–1606` 成功讀取合法空單或候選全被過濾時會清除 gap；`:1652–1660` 卻以 `planned_count > 0` 判斷修復成功，否則印出 `still unresolved (no orders found)`。因此狀態／監控已恢復健康，操作日誌仍聲稱缺單。應依 gap 是否仍存在決定訊息，將合法零訊號與未找到來源分開。這不否定核心補跑成功，但屬於新可觀測性回歸。本次臨時帳戶實測：

```text
Notice: Run close-and-plan:2026-09-02 planning gap for 2026-09-02 still unresolved (no orders found).
EMPTY_BACKFILL_GAPS []
exit 0
```

### F4 — r2 上線條件仍待補齊（未在三 commits 中修改）

- `run_mr20.sh:35/67` 仍使用主機日期，未固定台北 TZ；`:45–48` 將 session helper 所有失敗當休市；`:81–86/115–120` 產單或 freshness 失敗仍 exit 0，且跳過收盤結算。這是 r2 F4 殘留。
- `.github` 與 `run_mr20*.sh` 搜尋沒有 `check_processed_runs` 呼叫；未讀 host crontab，因此結論是「缺部署證據」，不是「host 必然沒排程」。
- `.github/workflows/update_ai_report.yml:135/212` 仍為 `git add -u artifacts`；`.gitignore:175` 仍忽略頂層 `artifacts/*.json`。三 commits 未提供新訂單的替代交付設定；離線 smoke 不驗證此交付。
- r2 F5 的無日期 `{"orders": []}` 自動搜尋相容性未改；新 smoke 仍以顯式空單路徑完成零訊號日，不能據此聲稱自動搜尋空單已修。

### F5 — P1：執行日 open 已完成後仍接受歷史 gap 補跑（新增）

`independent_sim.py:1647–1649` 只檢查 gap，沒有檢查其目標 open 是否已完成；`:1601` 仍依舊 as_of 建立 D+1 pending，並清除 gap。重現順序為 D 缺單 → D+1 open（零單完成）→ 顯式補跑 D。補跑宣稱成功，但 pending 的執行日已處理，`:1517` 又禁止該 open 重跑。這會把未完成的規劃告警轉成無法正常成交的 pending；屬於新開放重試路徑的時序漏洞。

本次主審在臨時帳戶重現：

```text
✅ Close-and-plan gap backfilled for 2026-09-02: 1 new orders planned.
PAST_OPEN_BACKFILL {'gaps': [], 'execution_dates': ['2026-09-03'], 'open_processed': True}
Notice: Run open:2026-09-03 was already processed. Idempotent skip.
exit 0
```

應在執行窗口已過或目標 open 已完成時拒絕補跑並保留缺口，或制定明確恢復政策；不可直接重跑已完成 open。新增測試未涵蓋此順序。純同日、尚未執行 open 的補跑修復仍然有效。

## 3. 範圍與 TDD 可信度

**修改範圍：PASS。** 三 commits 只改 7 檔：sim、paper、監控及四個相關測試檔。未修改 strategy/、eval_trader.py 或 event_backtest.py，沒有策略參數調整。前視問題仍是既有後續工作，不要求本輪越界修正。

**TDD：PARTIAL；回歸辨識力可信，但無法證明作者先寫測試、先跑紅。** 各 commit 同時包含測試及實作，commit 訊息不能取代歷史紅燈紀錄。本次將 HEAD 測試放入各 parent 的臨時 `git archive` 快照，獨立確認以下結果；沒有 checkout 或改寫工作樹。

| 測試／基準 | 實際反向輸出 | 判定 |
|---|---|---|
| 晚到顯式訂單補跑／`a63681c` | `1 failed, 10 deselected in 1.23s`，exit 1；gap 仍為 `['2026-09-02']`，原版印 idempotent skip | PASS：行為真紅，HEAD 綠 |
| gap 監控相關測試／`6fe7f86` | `2 failed, 1 passed, 6 deselected in 2.37s`，exit 1；函式回傳空、CLI 錯誤 exit 0 | PASS：函式和 CLI 真紅，HEAD 綠 |
| 上層 fallback 三案例／`559901a` | `2 failed, 1 passed in 0.81s`，exit 1；過期與空單皆錯誤輸出 HTML 買單 | PASS：上層真紅，HEAD 綠 |
| 新三跳 happy path／`6fe7f86` | `1 passed, 1 deselected in 1.15s`，exit 0 | 有效增加整合覆蓋；原本產單能運作，故不是生產程式 bug 的紅→綠證據 |

補跑測試只用空帳戶與 equity 列數驗證不重複結算；程式分支確實繞過結算，但仍欠實際持倉、pending 到期與合法零單補跑訊息的邊界測試。paper 測試未包含本報告 F1/F2。

## 4. 本次驗證

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider test_paper_tracker_edge_cases.py tests/test_orders_resolution.py tests/test_as_of_alignment.py tests/test_check_processed_runs.py tests/test_e2e_mr20_smoke.py tests/test_independent_sim.py tests/test_independent_sim_strategy_switch.py tests/test_paper_tracker_turnover.py tests/test_mr20_strategy.py tests/test_ai_report_orders.py
157 passed in 12.44s
exit 0

bash -n run_mr20.sh run_mr20_full.sh
無輸出，exit 0

git diff --exit-code a63681c c9db230 -- strategy/ eval_trader.py event_backtest.py
無輸出，exit 0

git diff --check a63681c c9db230
無輸出，exit 0
```

未執行全 repo 測試、build 或全域 lint；未連網產單、執行正式 runner、修改帳本或發送通知。測試通過不抵銷已重現的 freshness 漏洞，也不代表排程／交付已驗收。放行前應封住來源驗證漏洞，補上補跑時序防護與修正錯誤訊息，並補齊 r2 尚缺的 runner 與每日監控／交付證據。
