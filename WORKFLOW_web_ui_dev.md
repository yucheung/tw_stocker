# tw_stocker Web UI 開發工作流

## 開發流程

### 1. agy 開發（主力）
```bash
agy -p "<任務描述>" \
  --model "gemini-3.7-flash-high" \
  --dangerously-skip-permissions \
  --print-timeout 30m \
  --add-dir /root/work/tw_stocker
```

### 2. Codex 審查（交叉驗證）
```bash
codex exec "Review the changes in /root/work/tw_stocker-web for bugs, security issues, and architecture concerns." \
  --sandbox read-only
```

### 3. Claude 備援（escalation）
```bash
claude -p "<複雜問題描述>" \
  --model opus \
  --allowedTools "Read,Edit,Bash" \
  --max-turns 20
```
⚠️ 使用 Claude 前必須通知使用者確認

## Phase 開發順序

### Phase 1 — 認證 + 唯讀儀表板（1-1.5 週）
1. 建 repo tw_stocker-web
2. Zero Trust Access 設定
3. Worker：JWT 驗證 + /api/me + users 表
4. D1 migration 0001
5. 頁面：Dashboard、Paper、Signals
6. VPS sync cron

### Phase 2 — 回測引擎上線（1.5-2.5 週）
1. VPS FastAPI 骨架 + cloudflared 隧道
2. 回測 job 佇列（含優先序）
3. 狀態主動推送（VPS → Worker → D1）
4. /backtest 頁面
5. 結果版本化 + 比較檢視

### Phase 3 — Paper 即時化 + 策略管理（1-2 週）
1. /paper 即時報價重算
2. /strategies 頁面
3. 角色權限分級
4. 參數變更版本化

### Phase 4 — 券商整合（2-4 週）
1. broker adapter 介面
2. CathayBroker 實作
3. 訂單審批佇列
4. 實單 dry-run 對比

## 驗證清單

每個 Phase 完成後：
- [ ] agy 任務完成（git log 確認）
- [ ] Codex review 通過（無 P0 問題）
- [ ] 驗收標準達成（見 PLAN_web_ui_v2.md）
- [ ] 通知使用者確認

## 注意事項

1. **agy 輸出是純文字**，無 JSON，需手動 parse
2. **agy 無 --max-turns**，用 --print-timeout 限制
3. **agy -p 需要 --dangerously-skip-permissions** 才能用工具
4. **Codex review 用 --sandbox read-only**，不改檔案
5. **Claude 備援前必須通知使用者**，等確認後才執行
