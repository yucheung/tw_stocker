#!/usr/bin/env python3
"""
Paper Trading 自動追蹤器 v8.5

每日收盤後執行，自動模擬 v8.5 策略的實盤績效：
1. 從 stock_report.html 擷取今日信號
2. 追蹤已持倉的 TP/SL/時間到期
3. 累積權益曲線到 paper_equity.json
4. 產出 paper_trading.html 績效網頁

使用方式:
  python paper_tracker.py              # 每日更新（GitHub Actions 自動執行）
  python paper_tracker.py --reset      # 清除所有記錄重新開始
"""

import json
import glob
import os
import re
import sys
from datetime import datetime, date, timedelta
import argparse
import pandas as pd

from strategy.order_execution import evaluate_buy_limit_at_open

DATA_FILE = 'paper_equity.json'
HTML_FILE = 'paper_trading.html'
MAX_POSITIONS = 7

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            data = json.load(f)
        data.setdefault('pending_orders', [])
        data.setdefault('order_events', [])
        return data
    return {
        'start_date': date.today().isoformat(),
        'initial_capital': 200_000,
        'capital': 200_000,
        'positions': {},          # {ticker: {entry, tp, sl, entry_date, shares, day_count}}
        'pending_orders': [],     # 待執行訂單（訊號日收盤限價，次日開盤 <= 限價成交，否則 09:30 撤單）
        'closed_trades': [],      # [{ticker, entry, exit, pnl_pct, reason, entry_date, exit_date}]
        'equity_curve': [],       # [{date, equity, capital, n_positions}]
        'daily_signals': [],      # [{date, tickers: [...]}]
        'order_events': [],       # append-only：每筆 due order 的最終成交/撤單事件
    }

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

def get_current_bars(tickers):
    """用 yfinance 取得最新 OHLC，用於 paper fills 與 mark-to-market。"""
    import yfinance as yf
    bars = {}
    if not tickers:
        return bars

    def download(symbols):
        try:
            return yf.download(symbols, period='5d', progress=False)
        except Exception:
            return None

    try:
        def read_bars(df, symbol_map):
            if df is None or df.empty:
                return {}
            parsed = {}
            for ticker, symbol in symbol_map.items():
                bar = {
                    'open': field_value(df, 'Open', symbol),
                    'high': field_value(df, 'High', symbol),
                    'low': field_value(df, 'Low', symbol),
                    'close': field_value(df, 'Close', symbol),
                }
                if bar['close'] is not None:
                    parsed[ticker] = bar
            return parsed

        def field_value(df, field, symbol):
            if isinstance(df.columns, pd.MultiIndex):
                if (field, symbol) not in df.columns:
                    return None
                series = df[(field, symbol)].dropna()
            elif field in df.columns:
                series = df[field].dropna()
            else:
                return None
            if len(series) == 0:
                return None
            return float(series.iloc[-1])

        tw_symbols = {t: f"{t}.TW" for t in tickers}
        bars.update(read_bars(download(list(tw_symbols.values())), tw_symbols))
        missing = [t for t in tickers if t not in bars]
        if missing:
            two_symbols = {t: f"{t}.TWO" for t in missing}
            bars.update(read_bars(download(list(two_symbols.values())), two_symbols))
    except Exception as e:
        print(f"   ⚠️ 價格下載失敗: {e}")
    return bars


def _opt_float(value, default=None):
    """寬鬆轉 float：None / 空字串 / 非法值一律回傳 default。"""
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f == f else default  # 排除 NaN


def recompute_tp_sl(order, open_price, atr_val):
    """以實際開盤價為錨，重算 TP/SL 價位。

    回測假設 next-open 進場，TP/SL 錨點也應是 open_price。
    ai_report.py 產出的 order 以收盤價為錨，這裡修正（開盤跳空時避免整組偏移）。
    舊訂單缺欄位時（None）由 _opt_float 退回保守預設值。
    """
    # A1: NaN ATR 防護 — NaN 與任何數值比較恆為 False（NaN > 0 / <= 0 皆 False），
    #     必須用「不等於自身」偵測，避免 NaN 穿透 `atr_val <= 0` 檢查。
    if atr_val is None or atr_val != atr_val or atr_val <= 0:
        # ATR 不可用，退回固定百分比 fallback
        # A2: 避免 `or default` 覆寫合法 0 值（_opt_float 回傳 0.0 時須保留）
        tp_pct = _opt_float(order.get('tp_pct'), 0.20)
        tp_pct = tp_pct if tp_pct is not None else 0.20
        sl_pct = _opt_float(order.get('sl_pct'), 0.20)
        sl_pct = sl_pct if sl_pct is not None else 0.20
        tp_price = open_price * (1 + tp_pct)
        sl_price = open_price * (1 - sl_pct)
    else:
        # ATR 可用：以開盤價為錨 ± ATR × multiplier
        tp_mult = _opt_float(order.get('tp_atr_mult'), 4.0)
        tp_mult = tp_mult if tp_mult is not None else 4.0
        sl_mult = _opt_float(order.get('sl_atr_mult'), 2.0)
        sl_mult = sl_mult if sl_mult is not None else 2.0
        tp_price = open_price + atr_val * tp_mult
        sl_price = open_price - atr_val * sl_mult
    # A3: SL ≤ 0 sanity check — ATR 過大或 sl_pct ≥ 1 皆可能讓 SL 非正，
    #     退回保守百分比 fallback（並對 sl_pct 再加一層保險）
    if sl_price <= 0:
        sl_pct = _opt_float(order.get('sl_pct'), 0.20)
        sl_pct = sl_pct if sl_pct is not None else 0.20
        if sl_pct >= 1:
            sl_pct = 0.20  # sl_pct ≥ 1 時 SL 恆 ≤ 0，強制退回 20%
        sl_price = open_price * (1 - sl_pct)
    return tp_price, sl_price


def get_current_prices(tickers):
    """Backward-compatible latest close lookup."""
    return {ticker: bar['close'] for ticker, bar in get_current_bars(tickers).items()}


def _resolve_order_limit_price(order):
    """限價 legacy fallback：limit_price -> reference_close -> entry；三者皆無效回傳 None。"""
    for key in ('limit_price', 'reference_close', 'entry'):
        val = _opt_float(order.get(key))
        if val is not None and val > 0:
            return val
    return None


def extract_signals_from_orders():
    """從 artifacts/orders_YYYYMMDD.json 擷取今日機器可讀訂單（買進限價單）。"""
    order_files = glob.glob('artifacts/orders_*.json')
    if not order_files:
        return []
    latest = max(order_files, key=os.path.getmtime)
    try:
        with open(latest, encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        print(f"   ⚠️ orders JSON 讀取失敗: {e}")
        return []

    signals = []
    for order in payload.get('orders', []):
        if order.get('side') != 'buy':
            continue
        ticker = order.get('ticker', '?')
        limit_price = _resolve_order_limit_price(order)
        if limit_price is None:
            print(f"   ⚠️ {ticker} 委託缺乏有效限價（limit_price/reference_close/entry 皆無效），略過")
            continue
        try:
            tp = float(order['tp_price'])
            sl = float(order['sl_price'])
        except (KeyError, TypeError, ValueError):
            print(f"   ⚠️ {ticker} 缺乏 tp_price/sl_price，略過")
            continue
        ref_close = order.get('reference_close')
        atr = order.get('atr')
        signals.append({
            'ticker': ticker,
            'entry': limit_price,
            'limit_price': limit_price,
            'tp': tp,
            'sl': sl,
            'reference_close': float(ref_close) if ref_close is not None else None,
            'atr': float(atr) if atr is not None else None,
            'gap_limit_atr': float(order.get('gap_limit_atr', 1.5)),
            'execution_date': order.get('execution_date'),
            'signal_date': order.get('signal_date'),
            'rank': order.get('rank'),
            'order_type': order.get('order_type', 'limit'),
            'entry_model': order.get('entry_model', 'signal_close_limit_next_open_v1'),
            'time_in_force': order.get('time_in_force', 'DAY_UNTIL_0930'),
            'cancel_time': order.get('cancel_time', '09:30:00'),
            'timezone': order.get('timezone', 'Asia/Taipei'),
            'max_hold_days': int(order.get('max_hold_days', 20)),
            'time_exit': order.get('time_exit'),
            # Sizing / TP/SL 重算參數（舊 orders JSON 沒有這些欄位 → None，
            # 開倉時由 recompute_tp_sl 與內建 sizing 各自退回保守 fallback）
            'position_size': _opt_float(order.get('position_size')),
            'regime_scale': _opt_float(order.get('regime_scale')),
            'tp_sl_mode': order.get('tp_sl_mode'),
            'tp_atr_mult': _opt_float(order.get('tp_atr_mult')),
            'sl_atr_mult': _opt_float(order.get('sl_atr_mult')),
            'tp_pct': _opt_float(order.get('tp_pct')),
            'sl_pct': _opt_float(order.get('sl_pct')),
        })
    if signals:
        print(f"   📦 使用 orders JSON: {latest}")
    return signals

def extract_signals_from_report():
    """從 stock_report.html 擷取今日買入信號。"""
    order_signals = extract_signals_from_orders()
    if order_signals:
        return order_signals

    report_path = 'stock_report.html'
    if not os.path.exists(report_path):
        return []

    with open(report_path) as f:
        html = f.read()

    # Format: <td>TICKER</td><td>SCORE</td><td>ENTRY</td><td>...建議買進...</td>
    #         <td>停利: TP ... 停損: SL ...</td>
    signals = []
    rows = re.findall(r'<tr>(.*?)</tr>', html, re.DOTALL)
    for row in rows:
        if '建議買進' not in row:
            continue
        ticker_m = re.search(r'<td>(\d{4})</td>', row)
        entry_m = re.findall(r'<td[^>]*>([\d\.]+)</td>', row)
        tp_m = re.search(r'停利.*?>([\d\.]+)<', row)
        sl_m = re.search(r'停損.*?>([\d\.]+)<', row)
        if ticker_m and len(entry_m) >= 3 and tp_m and sl_m:
            signals.append({
                'ticker': ticker_m.group(1),
                'entry': float(entry_m[2]),  # third number is entry price (1st=ticker, 2nd=score, 3rd=price)
                'tp': float(tp_m.group(1)),
                'sl': float(sl_m.group(1)),
                'max_hold_days': 20,
            })
    return signals

def update_tracker(data):
    """主要更新邏輯：追蹤持倉、結算已平倉、記錄新信號。"""
    today = date.today().isoformat()
    buy_cost_rate = 0.001425
    sell_cost_rate = 0.004425
    slippage = 0.003
    max_hold = 20
    reserve_ratio = 0.10
    reserve_cash = data['initial_capital'] * reserve_ratio

    print(f"📊 Paper Tracker 更新 ({today})")
    print(f"   初始資金: {data['initial_capital']:,.0f}")
    print(f"   當前現金: {data['capital']:,.0f}")
    print(f"   保留現金: {reserve_cash:,.0f} ({reserve_ratio:.0%} 本金)")
    print(f"   持倉檔數: {len(data['positions'])}")

    # 0. 避免重複執行
    if data['equity_curve'] and data['equity_curve'][-1].get('date') == today:
        print(f"   ⚠️ 今日已更新過，跳過")
        return

    # 1. 取得所有相關股票的最新價格
    all_tickers = list(data['positions'].keys())
    signals = extract_signals_from_report()
    signal_tickers = [s['ticker'] for s in signals]
    pending_orders = data.get('pending_orders', [])
    pending_tickers = [o['ticker'] for o in pending_orders]
    all_tickers_set = set(all_tickers + signal_tickers + pending_tickers)
    bars = get_current_bars(list(all_tickers_set))
    prices = {ticker: bar['close'] for ticker, bar in bars.items()}

    # 2. 追蹤已持倉：檢查 TP/SL/時間到期
    to_close = []
    for ticker, pos in data['positions'].items():
        pos['day_count'] = pos.get('day_count', 0) + 1
        bar = bars.get(ticker)
        if bar is None or bar.get('close') is None:
            continue

        reason = None
        exit_price = bar['close']
        pos_max_hold = pos.get('max_hold_days', max_hold)
        # Conservative same-day ordering: SL before TP, matching backtest.
        if bar.get('low') is not None and bar['low'] <= pos['sl']:
            reason = 'SL'
            open_price = bar.get('open')
            exit_price = open_price if open_price is not None and open_price < pos['sl'] else pos['sl']
        elif bar.get('high') is not None and bar['high'] >= pos['tp']:
            reason = 'TP'
            open_price = bar.get('open')
            exit_price = open_price if open_price is not None and open_price > pos['tp'] else pos['tp']
        elif pos['day_count'] >= pos_max_hold:
            reason = 'TIME'
            exit_price = bar['close']

        if reason:
            # 計算 PnL（買進端不再含 slippage，見下方限價單成交邏輯）
            sell_cost = exit_price * pos['shares'] * sell_cost_rate
            slippage_cost = exit_price * pos['shares'] * slippage
            proceeds = exit_price * pos['shares'] - sell_cost - slippage_cost
            cost_basis = pos['entry'] * pos['shares'] * (1 + buy_cost_rate)
            pnl = proceeds - cost_basis
            pnl_pct = (exit_price / pos['entry'] - 1) * 100

            data['capital'] += proceeds
            data['closed_trades'].append({
                'ticker': ticker,
                'entry': pos['entry'],
                'exit': exit_price,
                'shares': pos['shares'],
                'pnl': round(pnl, 0),
                'pnl_pct': round(pnl_pct, 2),
                'reason': reason,
                'entry_date': pos['entry_date'],
                'exit_date': today,
                'days_held': pos['day_count'],
            })
            to_close.append(ticker)
            emoji = '🟢' if pnl > 0 else '🔴'
            print(f"   {emoji} 平倉 {ticker}: {pos['entry']:.1f}→{exit_price:.1f} ({pnl_pct:+.1f}%) [{reason}] 持{pos['day_count']}天")

    for t in to_close:
        del data['positions'][t]

    # 3. 執行到期的待執行訂單（訊號日收盤限價單模型 signal_close_limit_next_open_v1）：
    #    limit_price = 訊號日收盤價；今日 open <= limit_price 以 open 成交，
    #    否則（含開盤價缺漏/非法）於 09:30 撤單。不再有雙邊 ATR gap filter，
    #    也不得以收盤價回補缺漏的開盤價。每筆 due order 只產生一個 terminal
    #    order_events，且執行後一律離開 pending_orders（成交/撤單皆不留到下一日）。
    due_orders = [o for o in pending_orders
                  if not o.get('execution_date') or o['execution_date'] <= today]
    deferred = [o for o in pending_orders
                if o.get('execution_date') and o['execution_date'] > today]
    order_events = data.setdefault('order_events', [])
    existing_event_ids = {e.get('order_id') for e in order_events}

    def _rank_key(o):
        rank = o.get('rank')
        return (rank is None, rank if rank is not None else 0)

    ordered_due = sorted(due_orders, key=_rank_key)
    opened = 0
    if ordered_due:
        for sig in ordered_due:
            ticker = sig['ticker']
            signal_date = sig.get('signal_date') or today
            order_id = f"{signal_date}:{ticker}:buy"
            if order_id in existing_event_ids:
                continue  # 同一 order_id 不重複寫入第二個 terminal event
            existing_event_ids.add(order_id)

            event_base = {
                'order_id': order_id,
                'ticker': ticker,
                'signal_date': signal_date,
                'execution_date': sig.get('execution_date') or today,
                'event_time': f"{today}T09:30:00+08:00",
            }

            limit_price = _resolve_order_limit_price(sig)
            if limit_price is None:
                print(f"   ⚠️ {ticker} 委託缺乏有效限價，略過")
                continue

            if ticker in data['positions'] or len(data['positions']) >= MAX_POSITIONS:
                order_events.append({
                    **event_base,
                    'limit_price': limit_price,
                    'open_price': None,
                    'status': 'CANCELLED_NO_CAPACITY',
                    'fill_price': None,
                })
                print(f"   ⏭️ 額滿/已持有撤單 {ticker}")
                continue

            bar = bars.get(ticker)
            open_price = bar.get('open') if bar else None
            decision = evaluate_buy_limit_at_open(limit_price, open_price)

            if not decision.filled:
                order_events.append({
                    **event_base,
                    'limit_price': limit_price,
                    'open_price': decision.open_price,
                    'status': decision.status,
                    'fill_price': None,
                })
                if decision.status == 'CANCELLED_OPEN_ABOVE_LIMIT':
                    print(f"   ⏭️ 開高撤單 {ticker}: open {open_price:.1f} > limit {limit_price:.1f}")
                else:
                    print(f"   ⏭️ 無開盤價撤單 {ticker}")
                continue

            fill_price = decision.fill_price

            # ── Position sizing 對齊回測（event_backtest.py:1049）──
            # trade_amount = current_equity × position_size × regime_scale
            # position_size / regime_scale 由 artifacts/orders JSON 帶入
            # （ai_report 在收盤後即算好下一場進場的 regime 曝險縮放）；
            # 舊訂單缺欄位時退回保守預設 0.10 / 1.0。
            current_equity = data['capital']
            for tkr, pos in data['positions'].items():
                px = prices.get(tkr, pos['entry'])
                current_equity += px * pos['shares']
            position_size = _opt_float(sig.get('position_size'), 0.10)
            regime_scale = _opt_float(sig.get('regime_scale'), 1.0)
            if position_size <= 0:
                position_size = 0.10
            if regime_scale <= 0:
                regime_scale = 1.0
            available_cash = max(data['capital'] - reserve_cash, 0)
            trade_amount = min(current_equity * position_size * regime_scale, available_cash)
            shares = int(trade_amount / fill_price)

            actual_trade_amount = shares * fill_price
            buy_cost = actual_trade_amount * buy_cost_rate  # 限價單買進不再加 slippage
            if shares <= 0 or data['capital'] - actual_trade_amount - buy_cost < reserve_cash:
                order_events.append({
                    **event_base,
                    'limit_price': limit_price,
                    'open_price': open_price,
                    'status': 'CANCELLED_INSUFFICIENT_CASH',
                    'fill_price': None,
                })
                print(f"   💵 資金不足撤單 {ticker}")
                continue

            data['capital'] -= (actual_trade_amount + buy_cost)
            # TP/SL 以實際成交價（fill_price = open）為錨重算
            # （ai_report 以收盤價為錨，開盤跳空時修正）
            atr_for_tp = _opt_float(sig.get('atr'))
            tp_new, sl_new = recompute_tp_sl(sig, fill_price, atr_for_tp)
            data['positions'][ticker] = {
                'entry': fill_price,
                'tp': tp_new,
                'sl': sl_new,
                'entry_date': today,
                'shares': shares,
                'day_count': 0,
                'max_hold_days': sig.get('max_hold_days', max_hold),
            }
            order_events.append({
                **event_base,
                'limit_price': limit_price,
                'open_price': open_price,
                'status': 'FILLED',
                'fill_price': fill_price,
            })
            opened += 1
            print(
                f"   🆕 開倉 {ticker} @ {fill_price:.1f} × {shares:,.0f} "
                f"(投入 {actual_trade_amount:,.0f}, TP {tp_new:.1f} / SL {sl_new:.1f})"
            )
        if opened:
            print(f"   ✅ 今日開倉 {opened} 檔（待執行 {len(ordered_due)} 筆）")
        else:
            print(f"   ⚠️ 待執行 {len(ordered_due)} 筆皆未成交（開高/缺價/資金/額滿）")

    # 4. 記錄今日訊號，登錄為待執行訂單（隔日開盤執行，對齊回測 next-open）
    if signals:
        data['daily_signals'].append({'date': today, 'tickers': signal_tickers})
        new_pending = [{
            'ticker': s['ticker'],
            'entry': s['entry'],
            'tp': s['tp'],
            'sl': s['sl'],
            'reference_close': s.get('reference_close', s['entry']),
            'atr': s.get('atr'),
            'gap_limit_atr': s.get('gap_limit_atr', 1.5),
            'execution_date': s.get('execution_date'),
            'max_hold_days': s.get('max_hold_days', max_hold),
            'signal_date': today,
            # TP/SL 重算與 sizing 參數：開倉時以實際開盤價為錨重算
            'position_size': s.get('position_size'),
            'regime_scale': s.get('regime_scale'),
            'tp_sl_mode': s.get('tp_sl_mode'),
            'tp_atr_mult': s.get('tp_atr_mult'),
            'sl_atr_mult': s.get('sl_atr_mult'),
            'tp_pct': s.get('tp_pct'),
            'sl_pct': s.get('sl_pct'),
        } for s in signals]
        # 今日訊號取代舊的待執行單（每交易日重新排序）；保留尚未到期者
        data['pending_orders'] = new_pending + deferred
        exec_date = signals[0].get('execution_date') or '次一交易日'
        print(f"   📥 已登錄 {len(new_pending)} 筆待執行訂單（{exec_date} 開盤執行）")
    else:
        data['pending_orders'] = deferred
        print(f"   📋 今日無信號")

    # 4. 計算今日總權益
    total_equity = data['capital']
    for ticker, pos in data['positions'].items():
        price = prices.get(ticker, pos['entry'])
        total_equity += price * pos['shares']

    data['equity_curve'].append({
        'date': today,
        'equity': round(total_equity, 0),
        'capital': round(data['capital'], 0),
        'n_positions': len(data['positions']),
        'n_closed_today': len(to_close),
    })

    total_return = (total_equity / data['initial_capital'] - 1) * 100
    print(f"\n   💰 總權益: {total_equity:,.0f} ({total_return:+.1f}%)")
    print(f"   📈 已完成交易: {len(data['closed_trades'])} 筆")


def generate_html(data):
    """產出 paper trading 績效網頁。"""
    today = date.today().isoformat()
    initial = data['initial_capital']
    equity_curve = data['equity_curve']

    if not equity_curve:
        return

    latest_equity = equity_curve[-1]['equity']
    total_return = (latest_equity / initial - 1) * 100

    # 計算統計
    trades = data['closed_trades']
    n_trades = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0
    avg_pnl = sum(t['pnl_pct'] for t in trades) / n_trades if n_trades else 0
    total_profit = sum(t['pnl'] for t in wins) if wins else 0
    total_loss = abs(sum(t['pnl'] for t in losses)) if losses else 1
    pf = total_profit / total_loss if total_loss > 0 else 0

    # MDD
    peak = initial
    mdd = 0
    for pt in equity_curve:
        if pt['equity'] > peak:
            peak = pt['equity']
        dd = (pt['equity'] - peak) / peak * 100
        if dd < mdd:
            mdd = dd

    # 年化 (簡化)
    n_days = len(equity_curve)
    ann_return = total_return * (252 / max(n_days, 1))

    # 權益曲線 JSON
    dates_json = json.dumps([p['date'] for p in equity_curve])
    equity_json = json.dumps([p['equity'] for p in equity_curve])
    benchmark_json = json.dumps([initial] * len(equity_curve))

    # 交易清單 (最近 30 筆)
    recent_trades = trades[-30:][::-1]
    trades_html = ""
    for t in recent_trades:
        color = '#4ade80' if t['pnl'] > 0 else '#f87171'
        emoji = '🟢' if t['pnl'] > 0 else '🔴'
        trades_html += f"""
        <tr>
            <td>{t['exit_date']}</td>
            <td><b>{t['ticker']}</b></td>
            <td>{t['entry']:.1f}</td>
            <td>{t['exit']:.1f}</td>
            <td style="color:{color};font-weight:700">{t['pnl_pct']:+.1f}%</td>
            <td>{t['reason']}</td>
            <td>{t['days_held']}天</td>
        </tr>"""

    # 持倉
    positions_html = ""
    for ticker, pos in data['positions'].items():
        positions_html += f"""
        <tr>
            <td><b>{ticker}</b></td>
            <td>{pos['entry']:.1f}</td>
            <td>{pos['tp']:.1f}</td>
            <td>{pos['sl']:.1f}</td>
            <td>{pos['entry_date']}</td>
            <td>{pos.get('day_count', 0)}天</td>
        </tr>"""

    if not positions_html:
        positions_html = '<tr><td colspan="6" style="text-align:center;color:#888">目前無持倉</td></tr>'

    # 待執行訂單（next-open：隔日開盤進場）
    pending_html = ""
    for o in data.get('pending_orders', []):
        pending_html += f"""
        <tr>
            <td><b>{o['ticker']}</b></td>
            <td>{o.get('reference_close', o.get('entry', 0)):.1f}</td>
            <td>{o['tp']:.1f}</td>
            <td>{o['sl']:.1f}</td>
            <td>{o.get('execution_date', '次一交易日')}</td>
        </tr>"""
    if not pending_html:
        pending_html = '<tr><td colspan="5" style="text-align:center;color:#888">無待執行訂單</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Paper Trading v8.5 — {today}</title>
    <meta name="description" content="TW Stocker v8.5 Paper Trading 實時績效追蹤">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #e2e8f0;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{ max-width: 1000px; margin: 0 auto; }}
        h1 {{
            font-size: 1.8rem;
            background: linear-gradient(90deg, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 6px;
        }}
        .subtitle {{ color: #94a3b8; margin-bottom: 24px; font-size: 0.9rem; }}
        .metrics {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            margin-bottom: 24px;
        }}
        .metric {{
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid rgba(100, 116, 139, 0.3);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
        }}
        .metric .label {{ color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; }}
        .metric .value {{ font-size: 1.5rem; font-weight: 700; margin-top: 4px; }}
        .metric .value.green {{ color: #4ade80; }}
        .metric .value.red {{ color: #f87171; }}
        .metric .value.blue {{ color: #60a5fa; }}
        .chart-box {{
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid rgba(100, 116, 139, 0.3);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }}
        .chart-box h2 {{ font-size: 1.1rem; margin-bottom: 12px; color: #cbd5e1; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
        }}
        th {{
            text-align: left;
            padding: 8px 10px;
            border-bottom: 2px solid #334155;
            color: #94a3b8;
            font-weight: 600;
        }}
        td {{
            padding: 8px 10px;
            border-bottom: 1px solid #1e293b;
        }}
        tr:hover {{ background: rgba(100, 116, 139, 0.1); }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.7rem;
            font-weight: 700;
        }}
        .badge-live {{ background: #22c55e33; color: #4ade80; }}
        .disclaimer {{
            margin-top: 24px;
            padding: 14px;
            background: rgba(251, 191, 36, 0.08);
            border: 1px solid rgba(251, 191, 36, 0.2);
            border-radius: 8px;
            font-size: 0.75rem;
            color: #fbbf24;
        }}
    </style>
</head>
<body>
<div class="container">
    <h1>📈 Paper Trading v8.5</h1>
    <p class="subtitle">
        <span class="badge badge-live">● LIVE</span>
        起始日 {data['start_date']} | 更新 {today} | 初始資金 {initial:,.0f} | 保留本金 10%，其餘投入
    </p>

    <div class="metrics">
        <div class="metric">
            <div class="label">總權益</div>
            <div class="value {'green' if total_return > 0 else 'red'}">{latest_equity:,.0f}</div>
        </div>
        <div class="metric">
            <div class="label">總報酬</div>
            <div class="value {'green' if total_return > 0 else 'red'}">{total_return:+.1f}%</div>
        </div>
        <div class="metric">
            <div class="label">年化報酬</div>
            <div class="value {'green' if ann_return > 0 else 'red'}">{ann_return:+.1f}%</div>
        </div>
        <div class="metric">
            <div class="label">最大回撤</div>
            <div class="value red">{mdd:.1f}%</div>
        </div>
        <div class="metric">
            <div class="label">勝率</div>
            <div class="value blue">{win_rate:.0f}%</div>
        </div>
        <div class="metric">
            <div class="label">交易數</div>
            <div class="value blue">{n_trades}</div>
        </div>
        <div class="metric">
            <div class="label">Profit Factor</div>
            <div class="value {'green' if pf > 1 else 'red'}">{pf:.2f}</div>
        </div>
        <div class="metric">
            <div class="label">持倉數</div>
            <div class="value blue">{len(data['positions'])}</div>
        </div>
    </div>

    <div class="chart-box">
        <h2>權益曲線</h2>
        <canvas id="equityChart" height="80"></canvas>
    </div>

    <div class="chart-box">
        <h2>🔓 目前持倉</h2>
        <table>
            <tr><th>股票</th><th>進場價</th><th>停利</th><th>停損</th><th>進場日</th><th>持有</th></tr>
            {positions_html}
        </table>
    </div>

    <div class="chart-box">
        <h2>⏳ 待執行訂單（隔日開盤進場）</h2>
        <table>
            <tr><th>股票</th><th>參考收盤</th><th>停利</th><th>停損</th><th>執行日</th></tr>
            {pending_html}
        </table>
    </div>

    <div class="chart-box">
        <h2>📋 近期交易（最近 30 筆）</h2>
        <table>
            <tr><th>日期</th><th>股票</th><th>進場</th><th>出場</th><th>損益</th><th>原因</th><th>持有</th></tr>
            {trades_html}
        </table>
    </div>

    <div class="disclaimer">
        ⚠️ <b>免責聲明：</b>此為 Paper Trading 模擬績效，非真實交易。歷史模擬不代表未來報酬。
        策略版本 v8.5 (Ablation-Proven)，含成本 0.58%/筆 + 10bps 滑價。投資有風險，決策請自行負責。
    </div>
</div>

<script>
const ctx = document.getElementById('equityChart').getContext('2d');
new Chart(ctx, {{
    type: 'line',
    data: {{
        labels: {dates_json},
        datasets: [{{
            label: 'Paper Trading 權益',
            data: {equity_json},
            borderColor: '#60a5fa',
            backgroundColor: 'rgba(96, 165, 250, 0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 2,
            borderWidth: 2,
        }}, {{
            label: '初始資金',
            data: {benchmark_json},
            borderColor: '#475569',
            borderDash: [5, 5],
            fill: false,
            pointRadius: 0,
            borderWidth: 1,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{
            legend: {{ labels: {{ color: '#94a3b8' }} }},
        }},
        scales: {{
            x: {{ ticks: {{ color: '#64748b', maxTicksLimit: 10 }}, grid: {{ color: '#1e293b' }} }},
            y: {{ ticks: {{ color: '#64748b', callback: v => (v/1000).toFixed(0)+'K' }}, grid: {{ color: '#1e293b' }} }},
        }}
    }}
}});
</script>
</body>
</html>"""

    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"   🌐 績效網頁已更新: {HTML_FILE}")


def main():
    parser = argparse.ArgumentParser(description='Paper Trading 自動追蹤器 v8.5')
    parser.add_argument('--reset', action='store_true', help='清除所有記錄重新開始')
    args = parser.parse_args()

    if args.reset:
        for f in [DATA_FILE, HTML_FILE]:
            if os.path.exists(f):
                os.remove(f)
        print("🔄 已清除所有 paper trading 記錄")
        return

    data = load_data()
    update_tracker(data)
    save_data(data)
    generate_html(data)
    print("✅ Paper Tracker 完成")


if __name__ == '__main__':
    main()
