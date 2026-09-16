#!/usr/bin/env python3
"""
TP/SL ATR 倍數參數掃描

目的：在固定的 Top-7 v8.5 動能策略設定下，系統性掃描 tp_atr_mult / sl_atr_mult
組合，找出 Sharpe 最佳且不劣於現行參數 (TP=4.0, SL=3.0) 回撤的候選值。

做法：資料下載、Universe 建構、特徵工程只做一次（這些步驟與 TP/SL 無關），
之後每個 (TP, SL) 組合只重新跑事件驅動回測 + 風險指標計算，避免 42 組
參數各自重複發送 42 次網路請求（walk_forward.py 用 subprocess 呼叫
ai_report.py 的作法在這裡太慢）。

其餘策略參數（Universe 池、regime filter、成本模型等）一律沿用
ai_report.py 自己的 argparse 預設值 (parse_args())，只覆寫本腳本
關心的日期範圍/Top-K，避免手動複製一份容易與正式版 v8.5 設定漂移。

限制：本腳本不修改 paper_tracker.py，只讀取 ai_report.py 匯出的函式。
"""

import argparse
import os
import sys

import pandas as pd

import ai_report as ar


def parse_sweep_args():
    parser = argparse.ArgumentParser(
        description='Top-7 v8.5 策略 TP/SL ATR 倍數參數掃描'
    )
    parser.add_argument(
        '--tp-grid', type=str, default='2.0,2.5,3.0,3.5,4.0,4.5,5.0',
        help='TP ATR 倍數掃描清單 (逗號分隔)'
    )
    parser.add_argument(
        '--sl-grid', type=str, default='1.5,2.0,2.5,3.0,3.5,4.0',
        help='SL ATR 倍數掃描清單 (逗號分隔)'
    )
    parser.add_argument(
        '--days', type=int, default=1500,
        help='回測回溯天數 (預設 1500，約等同 walk_forward.py 預設 anchor 區間 2022-07 ~ 今天)'
    )
    parser.add_argument(
        '--start-date', type=str, default=None,
        help='明確指定回測起始日期 (YYYY-MM-DD，優先於 --days)'
    )
    parser.add_argument(
        '--end-date', type=str, default=None,
        help='明確指定回測結束日期 (YYYY-MM-DD，預設今天)'
    )
    parser.add_argument(
        '--top-k', type=int, default=7,
        help='每日最多進場股票數 (預設 7，對應 Top-7 策略)'
    )
    parser.add_argument(
        '--output', type=str, default='artifacts/param_sweep_results.csv',
        help='結果 CSV 輸出路徑'
    )
    return parser.parse_args()


def build_base_args(sweep_args):
    """透過 ai_report.py 自己的 parse_args() 取得其餘策略參數的官方預設值。"""
    argv = ['ai_report.py', '--days', str(sweep_args.days),
            '--top-k', str(sweep_args.top_k)]
    if sweep_args.start_date:
        argv += ['--start-date', sweep_args.start_date]
    if sweep_args.end_date:
        argv += ['--end-date', sweep_args.end_date]

    old_argv = sys.argv
    try:
        sys.argv = argv
        base_args = ar.parse_args()
    finally:
        sys.argv = old_argv
    return base_args


def prepare_market_data(base_args):
    """Phase 1~3.5：資料下載、Universe、資料完整性閘門、特徵工程，只跑一次。"""
    if base_args.static_pool or base_args.tickers:
        tickers = base_args.tickers if base_args.tickers else ar.DEFAULT_TICKERS
        use_dynamic = False
    else:
        print('建構動態 Universe 母體...')
        tickers = ar.resolve_dynamic_pool(
            pool=base_args.pool,
            listed_before=base_args.end_date,
            refresh=base_args.refresh_listing,
        )
        use_dynamic = True

    close_df, open_df, high_df, low_df, vol_df = ar.fetch_panel_data(
        tickers, days=base_args.days,
        start_date=base_args.start_date, end_date=base_args.end_date,
    )

    if use_dynamic:
        universe_mask = ar.build_liquid_universe(close_df, vol_df, top_n=base_args.universe_size)
    else:
        universe_mask = None

    if base_args.skip_data_gate:
        print('已停用資料完整性閘門 (--skip-data-gate)')
    else:
        print('資料完整性檢查...')
        ar.enforce_data_integrity(
            close_df, tickers,
            universe_mask=universe_mask,
            universe_size=base_args.universe_size if use_dynamic else None,
            min_coverage=base_args.min_data_coverage,
        )

    market_close = None
    if base_args.regime_filter or base_args.residual_momentum:
        print('下載大盤指數 (0050) 用於 regime filter...')
        bench_raw = ar.fetch_benchmark(
            '0050', days=base_args.days,
            start_date=base_args.start_date, end_date=base_args.end_date,
        )
        if len(bench_raw) > 0:
            market_close = bench_raw * bench_raw.iloc[0]

    total_score, ma_60, atr_df, short_ma = ar.engineer_features(
        close_df, vol_df, universe_mask,
        ma_period=base_args.ma_period,
        multi_ma=base_args.multi_ma,
        ml_weights=base_args.ml_weights,
        inst_flow_weight=base_args.inst_flow,
        inst_flow_df=None,
        residual_momentum=base_args.residual_momentum,
        trend_quality=base_args.trend_quality,
        liq_stability=base_args.liq_stability,
        liq_mode=base_args.liq_mode,
        market_close=market_close,
        rsi_weight=base_args.rsi_weight,
        breakout_weight=base_args.breakout_weight,
        value_weight=base_args.value_weight,
        rev_momentum_weight=base_args.rev_momentum_weight,
    )

    return {
        'close_df': close_df, 'open_df': open_df, 'high_df': high_df,
        'low_df': low_df, 'vol_df': vol_df,
        'universe_mask': universe_mask, 'market_close': market_close,
        'total_score': total_score, 'ma_60': ma_60,
    }


def run_one_combo(base_args, market_data, tp_mult, sl_mult):
    """用給定的 TP/SL ATR 倍數重跑事件驅動回測，回傳單列績效字典。"""
    backtester = ar.EventDrivenBacktester(
        tp_pct=base_args.tp,
        sl_pct=base_args.sl,
        max_hold_days=base_args.hold_days,
        initial_capital=base_args.capital,
        position_size=base_args.position_size,
        tp_sl_mode=base_args.tp_sl_mode,
        tp_atr_mult=tp_mult,
        sl_atr_mult=sl_mult,
        trailing_stop=base_args.trailing,
        trailing_atr_mult=base_args.trailing_atr,
        regime_filter=base_args.regime_filter,
        regime_graduated=base_args.regime_graduated,
        regime_floor=base_args.regime_floor,
        gap_filter_atr=base_args.gap_filter,
        volume_confirm=base_args.volume_confirm,
        blacklist_lookback=base_args.blacklist,
        breakeven_pct=base_args.breakeven,
        slippage=base_args.slippage,
        vol_parity=base_args.vol_parity,
        mean_reversion=base_args.mean_reversion,
        dynamic_risk=base_args.dynamic_risk,
        futures_hedge=base_args.futures_hedge,
        dd_pause_pct=base_args.dd_pause_pct,
        dd_pause_days=base_args.dd_pause_days,
        consec_loss_limit=base_args.consec_loss_limit,
        consec_loss_pause=base_args.consec_loss_pause,
        sector_max_pct=base_args.sector_max_pct,
        corr_filter=base_args.corr_filter,
        max_portfolio_heat=base_args.max_heat,
        rank_weighted=base_args.rank_weight,
        regime_deleverage=base_args.regime_delev,
        confidence_k=base_args.confidence_k,
        mid_hold_review=base_args.mid_hold_review,
        breadth_regime=base_args.breadth_regime,
        candidate_breadth=base_args.candidate_breadth,
        theme_breadth=base_args.theme_breadth,
        dynamic_sector_cap=base_args.dynamic_sector_cap,
        gap_aware_sizing=base_args.gap_aware_sizing,
        cluster_penalty=base_args.cluster_penalty,
        macro_regime=base_args.macro_regime,
        batch_entry=base_args.batch_entry,
        dynamic_topk=base_args.dynamic_topk,
        dynamic_gap_filter=base_args.dynamic_gap_filter,
        dynamic_corr_filter=base_args.dynamic_corr_filter,
        sector_flow_tilt=base_args.sector_flow_tilt,
        tilt_strength=base_args.tilt_strength,
        tilt_windows=[int(w) for w in base_args.tilt_windows.split(',')],
        buy_cost=base_args.buy_cost,
        sell_cost=base_args.sell_cost,
    )

    trades_df, equity_df, _ = backtester.run(
        market_data['total_score'], market_data['close_df'], market_data['open_df'],
        market_data['high_df'], market_data['low_df'], market_data['ma_60'],
        top_k=base_args.top_k,
        threshold=base_args.threshold,
        market_close=market_data['market_close'],
        vol_df=market_data['vol_df'],
        universe_mask=market_data['universe_mask'],
    )

    metrics = ar.compute_risk_metrics(equity_df, trades_df, base_args.capital)

    return {
        'tp_mult': tp_mult,
        'sl_mult': sl_mult,
        'total_return_pct': round(metrics['total_return'] * 100, 2),
        'ann_return_pct': round(metrics['ann_return'] * 100, 2),
        'sharpe': round(metrics['sharpe'], 3),
        'sortino': round(metrics['sortino'], 3),
        'calmar': round(metrics['calmar'], 3),
        'mdd_pct': round(metrics['max_drawdown_pct'] * 100, 2),
        'win_rate_pct': round(metrics['win_rate'] * 100, 1),
        'profit_factor': round(metrics['profit_factor'], 2) if metrics['profit_factor'] != float('inf') else float('inf'),
        'total_trades': metrics['total_trades'],
    }


def analyze_results(df, baseline_tp=4.0, baseline_sl=3.0):
    """比較掃描結果與現行參數 (4.0/3.0)，輸出建議文字。"""
    lines = []
    top3 = df.sort_values('sharpe', ascending=False).head(3)
    lines.append('Top 3 (依 Sharpe 排序):')
    for _, row in top3.iterrows():
        lines.append(
            f"  TP={row['tp_mult']:.1f} SL={row['sl_mult']:.1f}  "
            f"Sharpe={row['sharpe']:.3f}  MDD={row['mdd_pct']:.1f}%  "
            f"Ann={row['ann_return_pct']:+.1f}%  Trades={row['total_trades']}"
        )

    baseline_match = df[(df['tp_mult'] == baseline_tp) & (df['sl_mult'] == baseline_sl)]
    if baseline_match.empty:
        lines.append(f"\n找不到現行參數 TP={baseline_tp}/SL={baseline_sl} 的掃描結果，無法比較。")
        return '\n'.join(lines)

    baseline = baseline_match.iloc[0]
    lines.append(
        f"\n現行參數 TP={baseline_tp}/SL={baseline_sl}: "
        f"Sharpe={baseline['sharpe']:.3f}  MDD={baseline['mdd_pct']:.1f}%"
    )

    # MDD 以帶負號的百分比儲存，數值越大 (越接近 0) 代表回撤越小。
    better = df[
        (df['sharpe'] > baseline['sharpe']) & (df['mdd_pct'] > baseline['mdd_pct'])
    ].sort_values('sharpe', ascending=False)

    if better.empty:
        lines.append('\n結論: 沒有組合能同時在 Sharpe 與 MDD 上優於現行參數 -> 建議維持現行參數 (Keep current)。')
    else:
        best = better.iloc[0]
        lines.append(
            f"\n結論: TP={best['tp_mult']:.1f}/SL={best['sl_mult']:.1f} "
            f"同時優於現行參數 (Sharpe {best['sharpe']:.3f} > {baseline['sharpe']:.3f}, "
            f"MDD {best['mdd_pct']:.1f}% > {baseline['mdd_pct']:.1f}%) "
            f"-> 建議改用 TP={best['tp_mult']:.1f}, SL={best['sl_mult']:.1f}。"
        )
    return '\n'.join(lines)


def main():
    sweep_args = parse_sweep_args()
    tp_grid = [float(x) for x in sweep_args.tp_grid.split(',')]
    sl_grid = [float(x) for x in sweep_args.sl_grid.split(',')]
    combos = [(tp, sl) for tp in tp_grid for sl in sl_grid]

    base_args = build_base_args(sweep_args)

    print('=' * 60)
    print('TP/SL ATR 倍數參數掃描')
    print(f"   TP 掃描: {tp_grid}")
    print(f"   SL 掃描: {sl_grid}")
    print(f"   組合數: {len(combos)}")
    print('=' * 60)

    market_data = prepare_market_data(base_args)

    results = []
    for i, (tp_mult, sl_mult) in enumerate(combos, 1):
        print(f"\n[{i}/{len(combos)}] TP={tp_mult} SL={sl_mult}")
        try:
            row = run_one_combo(base_args, market_data, tp_mult, sl_mult)
        except Exception as exc:
            print(f"   失敗: {exc}")
            continue
        results.append(row)
        print(f"   Sharpe={row['sharpe']:.3f}  MDD={row['mdd_pct']:.1f}%  "
              f"Ann={row['ann_return_pct']:+.1f}%  Trades={row['total_trades']}")

    df = pd.DataFrame(results).sort_values('sharpe', ascending=False)

    output_dir = os.path.dirname(sweep_args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    df.to_csv(sweep_args.output, index=False)
    print(f"\n結果已寫入: {sweep_args.output}")

    print('\n' + '=' * 60)
    print('分析結果')
    print('=' * 60)
    print(analyze_results(df))


if __name__ == '__main__':
    main()
