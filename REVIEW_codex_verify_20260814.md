# tw_stocker v8.5 Codex 交叉驗證報告
> 日期: 2026-08-14 | 模型: Codex (GPT) | 驗證者: Hermes 派工

    29	    "sortino": 0.022872433068047385,
    30	    "calmar": 0.014475216046162278,
    31	    "max_drawdown_pct": -0.03411968598801729,
    32	    "total_trades": 34
    33	  },
    34	  "artifacts": {
    35	    "equity": "artifacts/equity_20260807.csv",
    36	    "trades": "artifacts/trades_20260807.csv",
    37	    "signals": "artifacts/signals_20260807.csv",
    38	    "orders": "artifacts/orders_20260807.json"
    39	  }
    40	}
     1	,Score
     2	2497,4.0
     1	Ticker,Entry_Date,Exit_Date,Entry_Price,Exit_Price,Return_Pct,Reason,Days_Held,TP_Price,SL_Price
     2	2497,2023-08-08,2023-08-14,64.42,59.3,-0.0858,🔴 停損,4,71.25,59.3
     3	2497,2023-09-05,2023-10-04,62.53,61.3,-0.0264,⚪ 時間到期,20,69.17,57.56
     4	2497,2023-10-04,2023-10-20,62.17,57.28,-0.085,🔴 停損,10,68.7,57.28
     5	2497,2023-10-26,2023-11-23,59.74,60.58,0.0071,⚪ 時間到期,20,65.59,55.36
     6	2497,2023-11-23,2023-12-21,59.83,57.8,-0.0407,⚪ 時間到期,20,64.55,56.3
     7	2497,2024-04-11,2024-04-16,53.09,49.88,-0.0669,🔴 停損,3,57.37,49.88
     8	2497,2024-05-03,2024-05-31,52.19,52.76,0.0041,⚪ 時間到期,20,59.27,46.87

codex
初步證據已確認：2026-08-07 metadata 明載 34 筆、Sharpe 0.03154；signals CSV 只有 2497 一列，HTML 也只顯示 2497。不過「只有一檔」是否等同「pipeline 靜默故障」仍要看資料抓取、例外處理與 universe 建構，不能只由低績效直接推定故障。現在進入原始碼因果核對。
exec
/bin/bash -lc "rg -n \"EXTENDED_TICKERS|TICKERS|universe|except|continue|download|yf\\.|score|threshold\" ai_report.py | head -240 && nl -ba ai_report.py | sed -n '1,260p'" in /root/work/tw_stocker
 succeeded in 0ms:
10:- Top-K 選股取代固定 threshold
21:    python ai_report.py --universe-size 100 --top-k 5
41:from strategy.ai_strategy import fetch_panel_data, engineer_features, build_liquid_universe
54:except ImportError:
63:DEFAULT_TICKERS = [
69:# 擴展股池：全 TWSE 主要個股（動態 universe 模式用）
71:EXTENDED_TICKERS = [
100:EXTENDED_TICKERS = list(dict.fromkeys(EXTENDED_TICKERS))
127:        except Exception:
143:    except Exception:
229:def generate_report(trades_df, equity_df, total_score, close_df, config,
351:    latest_date = total_score.index[-1]
352:    today_scores = total_score.loc[latest_date].dropna().sort_values(ascending=False)
353:    threshold = config.get('threshold', 2.0)
356:    # Step 1: 篩選候選（score >= threshold + close > MA）
359:    for ticker, score in today_scores.items():
362:            continue
367:        if score >= threshold and above_ma:
368:            candidates.append((ticker, score, price))
369:        elif score >= threshold:
370:            filtered_out.append((ticker, score, price, '低於 MA'))
372:            filtered_out.append((ticker, score, price, '評分不足'))
386:    except Exception:
390:    except Exception:
398:    for rank, (ticker, score, price) in enumerate(selected, 1):
475:            f'<tr><td>{ticker}</td><td>{score:.2f}</td>'
486:                'score': round(float(score), 4),
501:    for ticker, score, price in not_selected[:5]:
520:            f'<tr style="opacity:0.6"><td>{ticker}</td><td>{score:.2f}</td>'
526:    for ticker, score, price, reason in filtered_out[:5]:
531:            f'<tr style="opacity:0.4"><td>{ticker}</td><td>{score:.2f}</td>'
605:        except Exception:
647:        except Exception:
681:        except Exception:
731:    except Exception:
792:    except Exception:
828:    except Exception:
870:    except Exception:
917:    except Exception:
951:    except Exception:
1441:    today_signals = total_score.loc[[latest_date]].T
1453:    except Exception:
1500:        '--universe-size', type=int, default=60,
1542:        '--threshold', type=float, default=2.0,
1714:        help='v8.5: Breadth-aware regime：用 universe 內部寬度修正 regime 判斷 (預設開啟)'
1818:        tickers = args.tickers if args.tickers else DEFAULT_TICKERS
1821:        tickers = EXTENDED_TICKERS
1824:    mode_str = f"動態 Universe (Top-{args.universe_size})" if use_dynamic else f"靜態 ({len(tickers)} 檔)"
1851:        universe_mask = build_liquid_universe(close_df, vol_df, top_n=args.universe_size)
1853:        universe_mask = None
1861:        except Exception as e:
1877:    total_score, ma_60, atr_df, short_ma = engineer_features(
1878:        close_df, vol_df, universe_mask,
1948:        total_score, close_df, open_df, high_df, low_df, ma_60,
1950:        threshold=args.threshold,
1953:        universe_mask=universe_mask,
1992:        'threshold': args.threshold,
2007:    generate_report(report_trades_df, report_equity_df, total_score, close_df, config,
     1	#!/usr/bin/env python3
     2	"""
     3	AI 台股實戰區間交易系統 v2 (Event-Driven Quantitative Trading Pipeline)
     4	
     5	完整管線：資料下載 → 動態 Universe → AI 特徵排名 → 事件驅動回測 → 風險分析 → HTML 報表
     6	
     7	v2 改進：
     8	- Entry 改為 t+1 open（對齊實盤）
     9	- 動態 Liquid Universe（全 TWSE，按流動性篩選 Top-N）
    10	- Top-K 選股取代固定 threshold
    11	- Equity-based position sizing
    12	- ATR 自適應 TP/SL
    13	- 台股交易成本（手續費 + 證交稅）
    14	- 完整風險指標（Sharpe/Sortino/MaxDD/Calmar）
    15	- 0050 Benchmark 對比
    16	- exchange_calendars 精確交易日
    17	
    18	使用方式：
    19	    python ai_report.py
    20	    python ai_report.py --tickers 2330 2317 2454 --static-pool
    21	    python ai_report.py --universe-size 100 --top-k 5
    22	"""
    23	
    24	import argparse
    25	import json
    26	import os
    27	import sys
    28	import subprocess
    29	from datetime import datetime, timedelta
    30	
    31	import matplotlib
    32	matplotlib.use('Agg')
    33	import matplotlib.pyplot as plt
    34	import matplotlib.dates as mdates
    35	import pandas as pd
    36	import numpy as np
    37	
    38	# 確保 strategy/ 可被 import
    39	sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    40	
    41	from strategy.ai_strategy import fetch_panel_data, engineer_features, build_liquid_universe
    42	from strategy.event_backtest import EventDrivenBacktester
    43	from strategy.evaluation import slice_evaluation_window
    44	from strategy.risk_metrics import compute_risk_metrics, format_metrics_summary
    45	from strategy.benchmark import fetch_benchmark, equal_weight_benchmark, compute_excess_return
    46	from strategy.institutional_flow import build_inst_flow_df, get_inst_flow_for_signals, fetch_inst_rankings
    47	from strategy.news_sentiment import get_news_sentiment_for_signals
    48	
    49	# 嘗試載入 exchange_calendars
    50	try:
    51	    import exchange_calendars as xcals
    52	    TW_CALENDAR = xcals.get_calendar('XTAI')
    53	    HAS_EXCHANGE_CAL = True
    54	except ImportError:
    55	    TW_CALENDAR = None
    56	    HAS_EXCHANGE_CAL = False
    57	    print("⚠️ exchange_calendars 未安裝，最晚出場日將使用近似計算")
    58	
    59	
    60	# ==========================================
    61	# 預設股池：熱門權值、AI、航運、金融股（靜態池模式用）
    62	# ==========================================
    63	DEFAULT_TICKERS = [
    64	    '2330', '2317', '2454', '2308', '2881',
    65	    '2603', '3231', '3481', '2382', '2609',
    66	    '2891', '1519', '2379', '2303',
    67	]
    68	
    69	# 擴展股池：全 TWSE 主要個股（動態 universe 模式用）
    70	# 包含上市 ETF、權值股、中型股，約 200 檔候選池
    71	EXTENDED_TICKERS = [
    72	    # 半導體
    73	    '2330', '2454', '2303', '3711', '2379', '6770', '3034', '2449',
    74	    '5274', '3529', '2408', '3443', '3035', '6415', '6525', '3661',
    75	    '3037', '2344', '6547',
    76	    # 電子
    77	    '2317', '2382', '2308', '2301', '2357', '2376', '2395', '3231',
    78	    '2474', '2353', '3481', '3017', '2345', '2383', '2356', '3044',
    79	    '2327', '3036', '2324', '2377', '2385', '2360', '2404',
    80	    '2412', '2459', '2458', '3045', '6505', '3023',
    81	    '3706', '3533', '2368', '4904', '4938', '6669',
    82	    # 金融
    83	    '2881', '2882', '2884', '2886', '2887', '2891', '2892',
    84	    '2880', '2883', '2885', '2888', '2889', '2890', '5880', '5876',
    85	    '2801', '2834', '2838', '2845', '2855', '2867', '2897',
    86	    # 傳產
    87	    '1301', '1303', '1326', '2002', '1101', '1102', '2912',
    88	    '1216', '2207', '9904', '1402', '9910', '1605', '2603',
    89	    '2609', '2615', '1519', '2606', '6005',
    90	    # 航運/觀光
    91	    '2618', '2610', '2605', '2634', '2637',
    92	    # 生技
    93	    '4142', '1760', '6446', '1707', '4743',
    94	    # 其他
    95	    '9945', '8454', '1504', '2105', '2201', '2204',
    96	    '5871', '6116', '6285', '3149', '6239',
    97	]
    98	
    99	# 去重
   100	EXTENDED_TICKERS = list(dict.fromkeys(EXTENDED_TICKERS))
   101	
   102	
   103	def get_next_n_trading_days(from_date, n_days):
   104	    """
   105	    使用 exchange_calendars 計算從 from_date 起的第 n 個交易日。
   106	
   107	    Parameters
   108	    ----------
   109	    from_date : datetime-like
   110	        起始日期
   111	    n_days : int
   112	        往後幾個交易日
   113	
   114	    Returns
   115	    -------
   116	    target_date : str
   117	        目標日期 (YYYY-MM-DD)
   118	    """
   119	    if HAS_EXCHANGE_CAL and TW_CALENDAR is not None:
   120	        try:
   121	            from_ts = pd.Timestamp(from_date)
   122	            # 取得足夠長的交易日列表
   123	            end_search = from_ts + pd.Timedelta(days=n_days * 2 + 30)
   124	            sessions = TW_CALENDAR.sessions_in_range(from_ts, end_search)
   125	            if len(sessions) > n_days:
   126	                return sessions[n_days].strftime('%Y-%m-%d')
   127	        except Exception:
   128	            pass
   129	
   130	    # Fallback: 用 1.4 倍近似
   131	    approx_date = pd.Timestamp(from_date) + timedelta(days=int(n_days * 1.4))
   132	    return approx_date.strftime('%Y-%m-%d')
   133	
   134	
   135	def _build_inst_section():
   136	    """
   137	    建立三大法人籌碼動態 HTML section。
   138	    從 tw-institutional-stocker 抓取 20 日持股變化排名，呈現買超/賣超 Top-15。
   139	    """
   140	    try:
   141	        up_list = fetch_inst_rankings(20, 'up') or []
   142	        down_list = fetch_inst_rankings(20, 'down') or []
   143	    except Exception:
   144	        return '<h2>🏛️ 三大法人籌碼動態</h2><p class="section-note">⚠️ 籌碼數據暫時無法取得</p>'
   145	
   146	    if not up_list and not down_list:
   147	        return '<h2>🏛️ 三大法人籌碼動態</h2><p class="section-note">⚠️ 無籌碼數據</p>'
   148	
   149	    # 過濾 ETF（code 5 碼以上通常為 ETF）
   150	    up_stocks = [x for x in up_list if len(x.get('code', '')) == 4][:15]
   151	    down_stocks = [x for x in down_list if len(x.get('code', '')) == 4][:15]
   152	
   153	    # 買超表
   154	    buy_rows = ""
   155	    for i, item in enumerate(up_stocks, 1):
   156	        code = item.get('code', '')
   157	        name = item.get('name', '')
   158	        change = item.get('change', 0.0)
   159	        ratio = item.get('three_inst_ratio', 0.0)
   160	        bar_width = min(change * 8, 100)
   161	        buy_rows += (
   162	            f'<tr>'
   163	            f'<td style="text-align:center;color:#888;">{i}</td>'
   164	            f'<td><b>{code}</b> {name}</td>'
   165	            f'<td style="text-align:right;color:#00ff00;font-weight:bold;">+{change:.1f}%</td>'
   166	            f'<td style="text-align:right;">{ratio:.1f}%</td>'
   167	            f'<td><div style="background:linear-gradient(90deg,#00ff0044 {bar_width}%,transparent {bar_width}%);'
   168	            f'height:18px;border-radius:3px;"></div></td>'
   169	            f'</tr>\n'
   170	        )
   171	
   172	    # 賣超表
   173	    sell_rows = ""
   174	    for i, item in enumerate(down_stocks, 1):
   175	        code = item.get('code', '')
   176	        name = item.get('name', '')
   177	        change = item.get('change', 0.0)
   178	        ratio = item.get('three_inst_ratio', 0.0)
   179	        bar_width = min(abs(change) * 8, 100)
   180	        sell_rows += (
   181	            f'<tr>'
   182	            f'<td style="text-align:center;color:#888;">{i}</td>'
   183	            f'<td><b>{code}</b> {name}</td>'
   184	            f'<td style="text-align:right;color:#ff4444;font-weight:bold;">-{abs(change):.1f}%</td>'
   185	            f'<td style="text-align:right;">{ratio:.1f}%</td>'
   186	            f'<td><div style="background:linear-gradient(90deg,#ff444444 {bar_width}%,transparent {bar_width}%);'
   187	            f'height:18px;border-radius:3px;"></div></td>'
   188	            f'</tr>\n'
   189	        )
   190	
   191	    html = f"""
   192	    <h2>🏛️ 三大法人籌碼動態</h2>
   193	    <p class="section-note">
   194	        近 20 日三大法人（外資+投信+自營商）持股比重變化排名。Data: <a href="https://github.com/voidful/tw-institutional-stocker" style="color:#4FC3F7;">tw-institutional-stocker</a>
   195	    </p>
   196	    <div class="split-grid">
   197	        <div>
   198	            <h3 style="color:#00ff00;margin-bottom:8px;">🟢 法人買超 Top-15（20日）</h3>
   199	            <table style="width:100%;">
   200	                <thead><tr>
   201	                    <th style="width:30px;">#</th>
   202	                    <th>股票</th>
   203	                    <th style="text-align:right;">變化</th>
   204	                    <th style="text-align:right;">持股</th>
   205	                    <th style="width:80px;">幅度</th>
   206	                </tr></thead>
   207	                <tbody>{buy_rows}</tbody>
   208	            </table>
   209	        </div>
   210	        <div>
   211	            <h3 style="color:#ff4444;margin-bottom:8px;">🔴 法人賣超 Top-15（20日）</h3>
   212	            <table style="width:100%;">
   213	                <thead><tr>
   214	                    <th style="width:30px;">#</th>
   215	                    <th>股票</th>
   216	                    <th style="text-align:right;">變化</th>
   217	                    <th style="text-align:right;">持股</th>
   218	                    <th style="width:80px;">幅度</th>
   219	                </tr></thead>
   220	                <tbody>{sell_rows}</tbody>
   221	            </table>
   222	        </div>
   223	    </div>
   224	"""
   225	    return html
   226	
   227	
   228	
   229	def generate_report(trades_df, equity_df, total_score, close_df, config,
   230	                    metrics, benchmark_equity=None, ew_equity=None,
   231	                    benchmark2_equity=None,
   232	                    high_df=None, low_df=None, show_inst=True):
   233	    """
   234	    產出 AI 交易計畫 HTML 報表與資金曲線圖（v2 完整版）。
   235	
   236	    Parameters
   237	    ----------
   238	    high_df, low_df : pd.DataFrame, optional
   239	        最高/最低價矩陣，用於精確 ATR 計算（對齊回測引擎）。
   240	    """
   241	    print("📊 產出 AI 交易計畫與績效報表...")
   242	
   243	    tp_pct = config['tp_pct']
   244	    sl_pct = config['sl_pct']
   245	    max_hold_days = config['max_hold_days']
   246	    initial_capital = config['initial_capital']
   247	    tp_sl_mode = config.get('tp_sl_mode', 'atr')
   248	    top_k = config.get('top_k', 3)
   249	
   250	    total_ret = metrics['total_return'] * 100
   251	
   252	    # === 計算精確 ATR（與回測引擎同公式） ===
   253	    def _compute_display_atr(close_s, high_s=None, low_s=None, period=20):
   254	        """計算單檔股票的 ATR，對齊 EventDrivenBacktester._compute_atr"""
   255	        if high_s is not None and low_s is not None:
   256	            prev_close = close_s.shift(1)
   257	            tr1 = high_s - low_s
   258	            tr2 = (high_s - prev_close).abs()
   259	            tr3 = (low_s - prev_close).abs()
   260	            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

exec
/bin/bash -lc "nl -ba ai_report.py | sed -n '1780,2025p'; nl -ba strategy/ai_strategy.py | sed -n '1,340p'" in /root/work/tw_stocker
 succeeded in 0ms:
  1780	    parser.add_argument(
  1781	        '--sector-flow-tilt', action='store_true',
  1782	        help='啟用板塊資金流傾斜：用 10/15/20 天動量追蹤板塊資金流向，動態分配 Top-K 配額'
  1783	    )
  1784	    parser.add_argument(
  1785	        '--tilt-strength', type=float, default=1.0,
  1786	        help='板塊傾斜力度 (0.0=均分, 1.0=全力傾斜, 預設 1.0)'
  1787	    )
  1788	    parser.add_argument(
  1789	        '--tilt-windows', type=str, default='10,15,20',
  1790	        help='板塊動量計算窗口 (逗號分隔, 預設 10,15,20)'
  1791	    )
  1792	    # === FinLab 啟發因子（Phase 8: 新因子維度）===
  1793	    parser.add_argument(
  1794	        '--rsi-weight', type=float, default=0.0,
  1795	        help='RSI-20 動量因子權重 (預設 0 = 停用; FinLab 用 RSI 選最強 20 檔)'
  1796	    )
  1797	    parser.add_argument(
  1798	        '--breakout-weight', type=float, default=0.0,
  1799	        help='300日創新高突破因子權重 (預設 0 = 停用; FinLab 年化 ~32%%)'
  1800	    )
  1801	    parser.add_argument(
  1802	        '--value-weight', type=float, default=0.0,
  1803	        help='PE/PB 價值因子權重 (預設 0 = 停用; FinLab 雙渦輪核心)'
  1804	    )
  1805	    parser.add_argument(
  1806	        '--rev-momentum-weight', type=float, default=0.0,
  1807	        help='60日營收動能代理因子權重 (預設 0 = 停用; FinLab MEMORY.md 鐵律)'
  1808	    )
  1809	
  1810	    return parser.parse_args()
  1811	
  1812	
  1813	def main():
  1814	    args = parse_args()
  1815	
  1816	    # 決定股池
  1817	    if args.static_pool or args.tickers:
  1818	        tickers = args.tickers if args.tickers else DEFAULT_TICKERS
  1819	        use_dynamic = False
  1820	    else:
  1821	        tickers = EXTENDED_TICKERS
  1822	        use_dynamic = True
  1823	
  1824	    mode_str = f"動態 Universe (Top-{args.universe_size})" if use_dynamic else f"靜態 ({len(tickers)} 檔)"
  1825	    tp_sl_str = f"ATR×{args.tp_atr}/{args.sl_atr}" if args.tp_sl_mode == 'atr' \
  1826	        else f"+{args.tp*100:.0f}%/-{args.sl*100:.0f}%"
  1827	    cost_str = f"買 {args.buy_cost*100:.3f}% 賣 {args.sell_cost*100:.3f}%"
  1828	
  1829	    trailing_str = f" +Trailing({args.trailing_atr}×ATR)" if args.trailing else ""
  1830	
  1831	    print("=" * 60)
  1832	    print("🎯 AI 台股量化交易系統 v8.5")
  1833	    print("=" * 60)
  1834	    print(f"   股池: {mode_str}")
  1835	    print(f"   TP/SL: {tp_sl_str}{trailing_str}  Top-K: {args.top_k}  持倉上限: {args.hold_days} 天")
  1836	    print(f"   成本: {cost_str}")
  1837	    if args.start_date:
  1838	        print(f"   回測期間: {args.start_date} → {args.end_date or '今天'}")
  1839	    else:
  1840	        print(f"   回測天數: {args.days}")
  1841	    print("=" * 60)
  1842	
  1843	    # Phase 1: 資料下載
  1844	    close_df, open_df, high_df, low_df, vol_df = fetch_panel_data(
  1845	        tickers, days=args.days,
  1846	        start_date=args.start_date, end_date=args.end_date,
  1847	    )
  1848	
  1849	    # Phase 2: 動態 Universe 或靜態池
  1850	    if use_dynamic:
  1851	        universe_mask = build_liquid_universe(close_df, vol_df, top_n=args.universe_size)
  1852	    else:
  1853	        universe_mask = None
  1854	
  1855	    # Phase 2.5: 籌碼時序數據（僅用於因子加權；報表顯示使用輕量 API）
  1856	    inst_flow_df = None
  1857	    if args.inst_flow > 0:
  1858	        try:
  1859	            inst_flow_df, inst_ratio_df = build_inst_flow_df(
  1860	                list(close_df.columns), close_df, verbose=True)
  1861	        except Exception as e:
  1862	            print(f"   ⚠️ 籌碼數據抓取失敗，跳過: {e}")
  1863	            inst_flow_df = None
  1864	
  1865	    # Phase 3.5: 提前下載 0050 用於 regime filter + 殘差動量
  1866	    market_close = None
  1867	    if args.regime_filter or args.residual_momentum:
  1868	        print("\n📊 下載大盤指數 (0050) 用於 regime filter...")
  1869	        bench_raw = fetch_benchmark(
  1870	            '0050', days=args.days,
  1871	            start_date=args.start_date, end_date=args.end_date,
  1872	        )
  1873	        if len(bench_raw) > 0:
  1874	            market_close = bench_raw * bench_raw.iloc[0]
  1875	
  1876	    # Phase 3: 特徵工程
  1877	    total_score, ma_60, atr_df, short_ma = engineer_features(
  1878	        close_df, vol_df, universe_mask,
  1879	        ma_period=args.ma_period,
  1880	        multi_ma=args.multi_ma,
  1881	        ml_weights=args.ml_weights,
  1882	        inst_flow_weight=args.inst_flow,
  1883	        inst_flow_df=inst_flow_df,
  1884	        residual_momentum=args.residual_momentum,
  1885	        trend_quality=args.trend_quality,
  1886	        liq_stability=args.liq_stability,
  1887	        liq_mode=args.liq_mode,
  1888	        market_close=market_close,
  1889	        rsi_weight=args.rsi_weight,
  1890	        breakout_weight=args.breakout_weight,
  1891	        value_weight=args.value_weight,
  1892	        rev_momentum_weight=args.rev_momentum_weight,
  1893	    )
  1894	
  1895	    # Phase 4: 事件驅動回測
  1896	    backtester = EventDrivenBacktester(
  1897	        tp_pct=args.tp,
  1898	        sl_pct=args.sl,
  1899	        max_hold_days=args.hold_days,
  1900	        initial_capital=args.capital,
  1901	        position_size=args.position_size,
  1902	        tp_sl_mode=args.tp_sl_mode,
  1903	        tp_atr_mult=args.tp_atr,
  1904	        sl_atr_mult=args.sl_atr,
  1905	        trailing_stop=args.trailing,
  1906	        trailing_atr_mult=args.trailing_atr,
  1907	        regime_filter=args.regime_filter,
  1908	        regime_graduated=args.regime_graduated,
  1909	        regime_floor=args.regime_floor,
  1910	        gap_filter_atr=args.gap_filter,
  1911	        volume_confirm=args.volume_confirm,
  1912	        blacklist_lookback=args.blacklist,
  1913	        breakeven_pct=args.breakeven,
  1914	        slippage=args.slippage,
  1915	        vol_parity=args.vol_parity,
  1916	        mean_reversion=args.mean_reversion,
  1917	        dynamic_risk=args.dynamic_risk,
  1918	        futures_hedge=args.futures_hedge,
  1919	        dd_pause_pct=args.dd_pause_pct,
  1920	        dd_pause_days=args.dd_pause_days,
  1921	        consec_loss_limit=args.consec_loss_limit,
  1922	        consec_loss_pause=args.consec_loss_pause,
  1923	        sector_max_pct=args.sector_max_pct,
  1924	        corr_filter=args.corr_filter,
  1925	        max_portfolio_heat=args.max_heat,
  1926	        rank_weighted=args.rank_weight,
  1927	        regime_deleverage=args.regime_delev,
  1928	        confidence_k=args.confidence_k,
  1929	        mid_hold_review=args.mid_hold_review,
  1930	        breadth_regime=args.breadth_regime,
  1931	        candidate_breadth=args.candidate_breadth,
  1932	        theme_breadth=args.theme_breadth,
  1933	        dynamic_sector_cap=args.dynamic_sector_cap,
  1934	        gap_aware_sizing=args.gap_aware_sizing,
  1935	        cluster_penalty=args.cluster_penalty,
  1936	        macro_regime=args.macro_regime,
  1937	        batch_entry=args.batch_entry,
  1938	        dynamic_topk=args.dynamic_topk,
  1939	        dynamic_gap_filter=args.dynamic_gap_filter,
  1940	        dynamic_corr_filter=args.dynamic_corr_filter,
  1941	        sector_flow_tilt=args.sector_flow_tilt,
  1942	        tilt_strength=args.tilt_strength,
  1943	        tilt_windows=[int(w) for w in args.tilt_windows.split(',')],
  1944	        buy_cost=args.buy_cost,
  1945	        sell_cost=args.sell_cost,
  1946	    )
  1947	    trades_df, equity_df = backtester.run(
  1948	        total_score, close_df, open_df, high_df, low_df, ma_60,
  1949	        top_k=args.top_k,
  1950	        threshold=args.threshold,
  1951	        market_close=market_close,
  1952	        vol_df=vol_df,
  1953	        universe_mask=universe_mask,
  1954	    )
  1955	
  1956	    report_equity_df, report_trades_df = slice_evaluation_window(
  1957	        equity_df, trades_df,
  1958	        eval_start=args.eval_start,
  1959	        initial_capital=args.capital,
  1960	    )
  1961	    if args.eval_start:
  1962	        print(f"\n📏 績效統計區間: {args.eval_start} → {args.end_date or '今天'} "
  1963	              f"(資料自 {args.start_date or f'近 {args.days} 天'} 暖機)")
  1964	
  1965	    # Phase 5: 風險指標
  1966	    metrics = compute_risk_metrics(report_equity_df, report_trades_df, args.capital)
  1967	    print(format_metrics_summary(metrics))
  1968	
  1969	    # Phase 6: Benchmark
  1970	    print("\n📊 載入 Benchmark 進行比較...")
  1971	    benchmark_start = args.eval_start or args.start_date
  1972	    benchmark_equity = fetch_benchmark(
  1973	        '0050', days=args.days,
  1974	        start_date=benchmark_start, end_date=args.end_date,
  1975	    )
  1976	    benchmark2_equity = fetch_benchmark(
  1977	        '00981A', days=args.days,
  1978	        start_date=benchmark_start, end_date=args.end_date,
  1979	    )
  1980	    ew_equity = equal_weight_benchmark(close_df)
  1981	    if benchmark_start:
  1982	        ew_equity = ew_equity.loc[pd.to_datetime(ew_equity.index) >= pd.Timestamp(benchmark_start)]
  1983	        if not ew_equity.empty:
  1984	            ew_equity = ew_equity / ew_equity.iloc[0]
  1985	
  1986	    # Phase 7: 報表產出
  1987	    config = {
  1988	        'tp_pct': args.tp,
  1989	        'sl_pct': args.sl,
  1990	        'max_hold_days': args.hold_days,
  1991	        'initial_capital': args.capital,
  1992	        'threshold': args.threshold,
  1993	        'tp_sl_mode': args.tp_sl_mode,
  1994	        'tp_atr_mult': args.tp_atr,
  1995	        'sl_atr_mult': args.sl_atr,
  1996	        'trailing_stop': args.trailing,
  1997	        'trailing_atr_mult': args.trailing_atr,
  1998	        'top_k': args.top_k,
  1999	        'buy_cost': args.buy_cost,
  2000	        'sell_cost': args.sell_cost,
  2001	        'gap_filter': args.gap_filter,
  2002	        'dynamic_gap_filter': args.dynamic_gap_filter,
  2003	        # 下一交易日進場實際採用的 gap filter 倍數（含 dynamic regime 放寬），
  2004	        # 由回測引擎以 latest_date 大盤資料算出，供 paper trading 精確對齊。
  2005	        'gap_filter_effective': backtester.next_session_gap_limit(),
  2006	    }
  2007	    generate_report(report_trades_df, report_equity_df, total_score, close_df, config,
  2008	                    metrics, benchmark_equity, ew_equity,
  2009	                    benchmark2_equity=benchmark2_equity,
  2010	                    high_df=high_df, low_df=low_df,
  2011	                    show_inst=args.show_inst)
  2012	    print("\n🚀 全部完成！請打開 stock_report.html 查看結果。")
  2013	
  2014	
  2015	if __name__ == '__main__':
  2016	    main()
     1	"""
     2	AI 多維度 Rank 橫向排名策略 (AI Ensemble Cross-Sectional Ranking) — v8.3
     3	
     4	Production scoring: rank_momentum × 3 + rank_trend × 1
     5	(可選: + rank_liq × 0.3 when liq_stability=True)
     6	
     7	已驗證無效 (Phase 1-4, 43 configs):
     8	- ml_weights, residual_momentum, trend_quality: 有害
     9	- breakeven, trailing, confidence-k, mid-hold-review: 有害或零效果
    10	"""
    11	
    12	STRATEGY_VERSION = "v8.5"
    13	
    14	import yfinance as yf
    15	import pandas as pd
    16	import numpy as np
    17	from datetime import datetime, timedelta
    18	import warnings
    19	
    20	warnings.filterwarnings('ignore')
    21	
    22	
    23	def fetch_panel_data(tickers, days=800, start_date=None, end_date=None):
    24	    """
    25	    批次下載多檔台股的 OHLCV 日線資料。
    26	
    27	    Parameters
    28	    ----------
    29	    tickers : list[str]
    30	        台股代號列表，例如 ['2330', '2317', '2454']
    31	    days : int
    32	        回溯天數，預設 800 天（約 3 年交易日）
    33	    start_date : str or datetime, optional
    34	        明確指定起始日期（優先於 days）
    35	    end_date : str or datetime, optional
    36	        明確指定結束日期（預設為今天）
    37	
    38	    Returns
    39	    -------
    40	    close_df, open_df, high_df, low_df, vol_df : tuple[pd.DataFrame]
    41	        各為 (日期 x 股票代號) 的 DataFrame，已做 forward fill
    42	    """
    43	    if end_date is not None:
    44	        end_dt = pd.Timestamp(end_date)
    45	    else:
    46	        end_dt = pd.Timestamp(datetime.today())
    47	
    48	    if start_date is not None:
    49	        start_dt = pd.Timestamp(start_date)
    50	        actual_days = (end_dt - start_dt).days
    51	    else:
    52	        start_dt = end_dt - timedelta(days=days)
    53	        actual_days = days
    54	
    55	    print(f"📥 正在批次下載 {len(tickers)} 檔股票的歷史資料 "
    56	          f"({start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}, "
    57	          f"~{actual_days} 天)...")
    58	
    59	    def _extract_field(raw_df, field):
    60	        if raw_df.empty:
    61	            return pd.DataFrame()
    62	        if isinstance(raw_df.columns, pd.MultiIndex):
    63	            try:
    64	                extracted = raw_df.xs(field, level=0, axis=1)
    65	            except KeyError:
    66	                return pd.DataFrame(index=raw_df.index)
    67	        elif field in raw_df.columns:
    68	            extracted = raw_df[[field]]
    69	        else:
    70	            return pd.DataFrame(index=raw_df.index)
    71	        extracted = extracted.copy()
    72	        extracted.columns = [
    73	            str(c).replace('.TW', '').replace('.TWO', '')
    74	            for c in extracted.columns
    75	        ]
    76	        if extracted.columns.duplicated().any():
    77	            extracted = extracted.T.groupby(level=0).first().T
    78	        return extracted
    79	
    80	    def _download_symbols(symbols):
    81	        downloaded = []
    82	        batch_size = 50
    83	        for batch_start in range(0, len(symbols), batch_size):
    84	            batch = symbols[batch_start:batch_start + batch_size]
    85	            batch_num = batch_start // batch_size + 1
    86	            total_batches = (len(symbols) + batch_size - 1) // batch_size
    87	            print(f"   📦 下載批次 {batch_num}/{total_batches} ({len(batch)} 檔)...")
    88	            batch_df = yf.download(batch, start=start_dt, end=end_dt, progress=False)
    89	            if not batch_df.empty:
    90	                downloaded.append(batch_df)
    91	        return downloaded
    92	
    93	    # yfinance 批次下載有大小限制，分批處理
    94	    tw_tickers = [f"{t}.TW" for t in tickers]
    95	    all_dfs = _download_symbols(tw_tickers)
    96	
    97	    if not all_dfs:
    98	        raise RuntimeError("無法下載任何資料")
    99	
   100	    # 合併所有批次
   101	    df = all_dfs[0] if len(all_dfs) == 1 else pd.concat(all_dfs, axis=1)
   102	
   103	    # 上櫃股票常用 .TWO 後綴；先抓 .TW，缺資料者再 fallback。
   104	    close_probe = _extract_field(df, 'Close')
   105	    missing_tickers = [
   106	        ticker for ticker in tickers
   107	        if ticker not in close_probe.columns or close_probe[ticker].dropna().empty
   108	    ]
   109	    if missing_tickers:
   110	        print(f"   🔁 .TW 無資料，改試 .TWO: {len(missing_tickers)} 檔")
   111	        two_dfs = _download_symbols([f"{t}.TWO" for t in missing_tickers])
   112	        if two_dfs:
   113	            df = pd.concat([df] + two_dfs, axis=1)
   114	
   115	    data = {}
   116	    for col in ['Close', 'Open', 'High', 'Low', 'Volume']:
   117	        temp_df = _extract_field(df, col)
   118	        if temp_df.empty:
   119	            print(f"   ⚠️ 欄位 {col} 不存在，跳過")
   120	            continue
   121	
   122	        # Keep tradable bars raw. Forward-filling Open/High/Low/Volume creates
   123	        # fake fills and fake liquidity on missing or suspended days. Close is
   124	        # only used as an indicator/marking input here, so allow a one-day carry
   125	        # to bridge isolated vendor gaps without creating long synthetic series.
   126	        if col == 'Close':
   127	            data[col] = temp_df.ffill(limit=1)
   128	        else:
   129	            data[col] = temp_df
   130	
   131	    print(f"   ✅ 下載完成，資料範圍：{data['Close'].index[0].strftime('%Y-%m-%d')}"
   132	          f" → {data['Close'].index[-1].strftime('%Y-%m-%d')}"
   133	          f"，共 {len(data['Close'].columns)} 檔")
   134	    return data['Close'], data['Open'], data['High'], data['Low'], data['Volume']
   135	
   136	
   137	def build_liquid_universe(close_df, vol_df, top_n=50, lookback=20):
   138	    """
   139	    建立動態流動性 Universe。
   140	
   141	    每日取「過去 lookback 日平均成交額 Top-N」作為當日可投資池。
   142	
   143	    Parameters
   144	    ----------
   145	    close_df : pd.DataFrame
   146	        收盤價矩陣
   147	    vol_df : pd.DataFrame
   148	        成交量矩陣
   149	    top_n : int
   150	        每日 universe 大小
   151	    lookback : int
   152	        成交額均值回溯期
   153	
   154	    Returns
   155	    -------
   156	    universe_mask : pd.DataFrame (bool)
   157	        (日期 x 股票) 的布林矩陣，True 代表當日在 universe 中
   158	    """
   159	    print(f"🌐 建立動態流動性 Universe (Top-{top_n}, 回溯 {lookback} 日)...")
   160	
   161	    # 平均成交額 = 收盤價 × 成交量 的 rolling mean
   162	    turnover = (close_df * vol_df).rolling(lookback).mean()
   163	
   164	    # 每日取 top_n
   165	    universe_mask = turnover.rank(axis=1, ascending=False) <= top_n
   166	
   167	    # 確保 NaN 的位置不被選入
   168	    universe_mask = universe_mask & close_df.notna() & (close_df > 0)
   169	
   170	    avg_size = universe_mask.sum(axis=1).mean()
   171	    print(f"   ✅ 動態 Universe 建立完成，平均每日 {avg_size:.0f} 檔")
   172	    return universe_mask
   173	
   174	
   175	def engineer_features(close_df, vol_df, universe_mask=None,
   176	                      ma_period=60, short_ma_period=20, multi_ma=False,
   177	                      ml_weights=False, inst_flow_weight=0.0,
   178	                      inst_flow_df=None,
   179	                      residual_momentum=False,
   180	                      trend_quality=False,
   181	                      liq_stability=False,
   182	                      liq_mode='raw',
   183	                      market_close=None,
   184	                      rsi_weight=0.0,
   185	                      breakout_weight=0.0,
   186	                      value_weight=0.0,
   187	                      rev_momentum_weight=0.0):
   188	    """
   189	    計算 AI 多維度特徵並做橫向百分位排名。
   190	
   191	    Parameters
   192	    ----------
   193	    close_df : pd.DataFrame
   194	        收盤價矩陣 (日期 x 股票代號)
   195	    vol_df : pd.DataFrame
   196	        成交量矩陣 (日期 x 股票代號)
   197	    universe_mask : pd.DataFrame (bool), optional
   198	        動態 Universe 遮罩。若提供，只在當日 universe 中做排名。
   199	    ma_period : int
   200	        主趨勢均線天數（預設 60）
   201	    short_ma_period : int
   202	        短期均線天數（用於多均線確認，預設 20）
   203	    multi_ma : bool
   204	        啟用多均線確認（short_ma > long_ma 才允許進場）
   205	    ml_weights : bool
   206	        啟用 ML 因子加權（LightGBM 取代等權加總）
   207	
   208	    Returns
   209	    -------
   210	    total_score : pd.DataFrame
   211	        各股票的 AI 綜合評分，日期 x 股票
   212	    ma_long : pd.DataFrame
   213	        主趨勢均線矩陣，用於進場信號過濾
   214	    atr_df : pd.DataFrame
   215	        20 日 ATR 矩陣，用於自適應 TP/SL 與 position sizing
   216	    short_ma : pd.DataFrame or None
   217	        短期均線矩陣（multi_ma=True 時有效）
   218	    """
   219	    print("🧠 正在計算多維度弱特徵與 Rank 排名...")
   220	
   221	    # === 原始指標計算 ===
   222	    # 1. 20 日動能：今天收盤 / 20 天前收盤
   223	    mom_20 = close_df / close_df.shift(20)
   224	
   225	    # 2. MA 乖離率：價格偏離均線的幅度
   226	    ma_long = close_df.rolling(ma_period).mean()
   227	    trend_bias = close_df / ma_long
   228	
   229	    # 3. 量能爆發比：5 日均量 / 20 日均量
   230	    vol_surge = vol_df.rolling(5).mean() / (vol_df.rolling(20).mean() + 1e-8)
   231	
   232	    # 4. 穩定度：波動率的倒數（越穩定越好）
   233	    volatility = close_df.pct_change().rolling(20).std()
   234	    stability = 1 / (volatility + 1e-8)
   235	
   236	    # 短期均線（多均線確認用）
   237	    short_ma = close_df.rolling(short_ma_period).mean() if multi_ma else None
   238	    ma_20 = close_df.rolling(20).mean()
   239	
   240	    # === ATR 計算 (用於 TP/SL 與 sizing) ===
   241	    atr_df = close_df.pct_change().abs().rolling(20).mean() * close_df
   242	
   243	    # === 殘差動量：扣除市場 beta ===
   244	    residual_mom = None
   245	    if residual_momentum and market_close is not None:
   246	        try:
   247	            stock_ret = close_df.pct_change()
   248	            mkt_ret = market_close.pct_change()
   249	            mkt_ret_aligned = mkt_ret.reindex(stock_ret.index, method='ffill')
   250	            stock_cum_20 = stock_ret.rolling(20).sum()
   251	            mkt_cum_20 = mkt_ret_aligned.rolling(20).sum()
   252	            residual_mom = stock_cum_20.sub(mkt_cum_20, axis=0)
   253	            print("   \U0001f52c 殘差動量已計算 (market-beta adjusted)")
   254	        except Exception as e:
   255	            print(f"   ⚠️ 殘差動量計算失敗: {e}")
   256	
   257	    # === 趨勢品質 ===
   258	    tq_score = None
   259	    if trend_quality:
   260	        try:
   261	            ma60_slope = (ma_long - ma_long.shift(5)) / (ma_long.shift(5) + 1e-8)
   262	            ma_alignment = ((close_df > ma_20) & (ma_20 > ma_long)).astype(float)
   263	            overheat = (close_df / ma_20 - 1).clip(lower=0)
   264	            overheat_penalty = 1 - overheat.clip(upper=0.15) / 0.15
   265	            tq_score = ma60_slope * 100 + ma_alignment * 0.5 + overheat_penalty * 0.3
   266	            print("   \U0001f4d0 趨勢品質已計算")
   267	        except Exception as e:
   268	            print(f"   ⚠️ 趨勢品質計算失敗: {e}")
   269	
   270	    # === 流動性穩定度 ===
   271	    liq_stab = None
   272	    if liq_stability:
   273	        try:
   274	            turnover = close_df * vol_df
   275	            raw_liq = turnover.rolling(20).mean() / (turnover.rolling(20).std() + 1e-8)
   276	
   277	            if liq_mode == 'demeaned':
   278	                # 殘差 liq: 扣除橫截面平均，保留個股相對穩定度
   279	                cross_mean = raw_liq.mean(axis=1)
   280	                liq_stab = raw_liq.sub(cross_mean, axis=0)
   281	                print("   \U0001f4a7 流動性穩定度已計算 (demeaned)")
   282	            elif liq_mode == 'sector':
   283	                # 行業中性: 電子 vs 非電子分開計算再合併
   284	                elec_prefixes = ('23','24','30','33','34','35','36','37',
   285	                                 '49','61','63','64','65','66','67','68','69')
   286	                elec_cols = [c for c in close_df.columns if str(c).startswith(elec_prefixes)]
   287	                non_elec_cols = [c for c in close_df.columns if c not in elec_cols]
   288	                liq_stab = raw_liq.copy()
   289	                if elec_cols:
   290	                    elec_mean = raw_liq[elec_cols].mean(axis=1)
   291	                    liq_stab[elec_cols] = raw_liq[elec_cols].sub(elec_mean, axis=0)
   292	                if non_elec_cols:
   293	                    ne_mean = raw_liq[non_elec_cols].mean(axis=1)
   294	                    liq_stab[non_elec_cols] = raw_liq[non_elec_cols].sub(ne_mean, axis=0)
   295	                print("   \U0001f4a7 流動性穩定度已計算 (sector-neutral)")
   296	            else:
   297	                liq_stab = raw_liq
   298	                print("   \U0001f4a7 流動性穩定度已計算 (raw)")
   299	        except Exception:
   300	            pass
   301	
   302	    # === 橫向百分位排名 ===
   303	    def _rank(df):
   304	        if universe_mask is not None:
   305	            return df.where(universe_mask).rank(axis=1, pct=True)
   306	        return df.rank(axis=1, pct=True)
   307	
   308	    rank_mom = _rank(mom_20)
   309	    rank_trend = _rank(trend_bias)
   310	    rank_res_mom = _rank(residual_mom) if residual_mom is not None else None
   311	    rank_tq = _rank(tq_score) if tq_score is not None else None
   312	    rank_liq = _rank(liq_stab) if liq_stab is not None else None
   313	
   314	    # === 籌碼因子排名 ===
   315	    rank_inst = None
   316	    if inst_flow_weight > 0 and inst_flow_df is not None:
   317	        inst_aligned = inst_flow_df.reindex(
   318	            index=close_df.index, columns=close_df.columns
   319	        )
   320	        rank_inst = _rank(inst_aligned)
   321	        print(f"   \U0001f3db\ufe0f 籌碼因子已載入 (weight={inst_flow_weight})")
   322	
   323	    # === FinLab 啟發因子（可選） ===
   324	    rank_rsi = None
   325	    rank_breakout = None
   326	    rank_value = None
   327	    rank_rev_mom = None
   328	
   329	    if rsi_weight > 0:
   330	        try:
   331	            from strategy.finlab_factors import compute_rsi_rank
   332	            rank_rsi = compute_rsi_rank(close_df, period=20, universe_mask=universe_mask)
   333	            print(f"   📈 RSI-20 因子已計算 (weight={rsi_weight})")
   334	        except Exception as e:
   335	            print(f"   ⚠️ RSI 因子計算失敗: {e}")
   336	
   337	    if breakout_weight > 0:
   338	        try:
   339	            from strategy.finlab_factors import compute_breakout_rank
   340	            rank_breakout = compute_breakout_rank(close_df, window=300, universe_mask=universe_mask)

exec
/bin/bash -lc "nl -ba strategy/ai_strategy.py | sed -n '300,490p'; python - <<'PY'
import ai_report
print('EXTENDED_TICKERS_COUNT', len(ai_report.EXTENDED_TICKERS))
PY" in /root/work/tw_stocker
 succeeded in 3237ms:
EXTENDED_TICKERS_COUNT 116

exec
/bin/bash -lc 'rg -n "position_size|regime|alloc|cash|shares|qty|quantity|budget|size|capital|equity" strategy/event_backtest.py paper_tracker.py | head -320' in /root/work/tw_stocker
 succeeded in 0ms:
paper_tracker.py:8:3. 累積權益曲線到 paper_equity.json
paper_tracker.py:25:DATA_FILE = 'paper_equity.json'
paper_tracker.py:34:        'initial_capital': 200_000,
paper_tracker.py:35:        'capital': 200_000,
paper_tracker.py:36:        'positions': {},          # {ticker: {entry, tp, sl, entry_date, shares, day_count}}
paper_tracker.py:39:        'equity_curve': [],       # [{date, equity, capital, n_positions}]
paper_tracker.py:182:    reserve_cash = data['initial_capital'] * reserve_ratio
paper_tracker.py:185:    print(f"   初始資金: {data['initial_capital']:,.0f}")
paper_tracker.py:186:    print(f"   當前現金: {data['capital']:,.0f}")
paper_tracker.py:187:    print(f"   保留現金: {reserve_cash:,.0f} ({reserve_ratio:.0%} 本金)")
paper_tracker.py:191:    if data['equity_curve'] and data['equity_curve'][-1].get('date') == today:
paper_tracker.py:231:            sell_cost = exit_price * pos['shares'] * sell_cost_rate
paper_tracker.py:232:            slippage_cost = exit_price * pos['shares'] * slippage
paper_tracker.py:233:            proceeds = exit_price * pos['shares'] - sell_cost - slippage_cost
paper_tracker.py:234:            cost_basis = pos['entry'] * pos['shares'] * (1 + buy_cost_rate + slippage)
paper_tracker.py:238:            data['capital'] += proceeds
paper_tracker.py:243:                'shares': pos['shares'],
paper_tracker.py:295:            available_cash = max(data['capital'] - reserve_cash, 0)
paper_tracker.py:297:            if available_cash <= 0:
paper_tracker.py:301:            gross_budget = available_cash / remaining_candidates
paper_tracker.py:302:            trade_amount = gross_budget / (1 + buy_cost_rate + slippage)
paper_tracker.py:303:            shares = int(trade_amount / entry_price)
paper_tracker.py:304:            if shares <= 0:
paper_tracker.py:308:            actual_trade_amount = shares * entry_price
paper_tracker.py:310:            if data['capital'] - actual_trade_amount - buy_cost < reserve_cash:
paper_tracker.py:315:            data['capital'] -= (actual_trade_amount + buy_cost)
paper_tracker.py:321:                'shares': shares,
paper_tracker.py:327:                f"   🆕 開倉 {ticker} @ {entry_price:.1f} × {shares:,.0f} "
paper_tracker.py:359:    total_equity = data['capital']
paper_tracker.py:362:        total_equity += price * pos['shares']
paper_tracker.py:364:    data['equity_curve'].append({
paper_tracker.py:366:        'equity': round(total_equity, 0),
paper_tracker.py:367:        'capital': round(data['capital'], 0),
paper_tracker.py:372:    total_return = (total_equity / data['initial_capital'] - 1) * 100
paper_tracker.py:373:    print(f"\n   💰 總權益: {total_equity:,.0f} ({total_return:+.1f}%)")
paper_tracker.py:380:    initial = data['initial_capital']
paper_tracker.py:381:    equity_curve = data['equity_curve']
paper_tracker.py:383:    if not equity_curve:
paper_tracker.py:386:    latest_equity = equity_curve[-1]['equity']
paper_tracker.py:387:    total_return = (latest_equity / initial - 1) * 100
paper_tracker.py:403:    for pt in equity_curve:
paper_tracker.py:404:        if pt['equity'] > peak:
paper_tracker.py:405:            peak = pt['equity']
paper_tracker.py:406:        dd = (pt['equity'] - peak) / peak * 100
paper_tracker.py:411:    n_days = len(equity_curve)
paper_tracker.py:415:    dates_json = json.dumps([p['date'] for p in equity_curve])
paper_tracker.py:416:    equity_json = json.dumps([p['equity'] for p in equity_curve])
paper_tracker.py:417:    benchmark_json = json.dumps([initial] * len(equity_curve))
paper_tracker.py:486:            font-size: 1.8rem;
paper_tracker.py:492:        .subtitle {{ color: #94a3b8; margin-bottom: 24px; font-size: 0.9rem; }}
paper_tracker.py:506:        .metric .label {{ color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; }}
paper_tracker.py:507:        .metric .value {{ font-size: 1.5rem; font-weight: 700; margin-top: 4px; }}
paper_tracker.py:518:        .chart-box h2 {{ font-size: 1.1rem; margin-bottom: 12px; color: #cbd5e1; }}
paper_tracker.py:522:            font-size: 0.85rem;
paper_tracker.py:540:            font-size: 0.7rem;
paper_tracker.py:550:            font-size: 0.75rem;
paper_tracker.py:566:            <div class="value {'green' if total_return > 0 else 'red'}">{latest_equity:,.0f}</div>
paper_tracker.py:600:        <canvas id="equityChart" height="80"></canvas>
paper_tracker.py:634:const ctx = document.getElementById('equityChart').getContext('2d');
paper_tracker.py:641:            data: {equity_json},
strategy/event_backtest.py:13:- Position sizing 改為 current equity based（非 initial capital）
strategy/event_backtest.py:41:    initial_capital : float
strategy/event_backtest.py:43:    position_size : float
strategy/event_backtest.py:55:    regime_filter : bool
strategy/event_backtest.py:74:        啟用動態風險預算（根據近 20 日 realized vol 調整 position size）
strategy/event_backtest.py:94:                 initial_capital=1_000_000, position_size=0.10,
strategy/event_backtest.py:97:                 regime_filter=False, regime_graduated=False,
strategy/event_backtest.py:98:                 regime_floor=0.30,
strategy/event_backtest.py:111:                 regime_deleverage=False,
strategy/event_backtest.py:114:                 breadth_regime=False,
strategy/event_backtest.py:120:                 macro_regime=False,
strategy/event_backtest.py:132:        self.initial_capital = initial_capital
strategy/event_backtest.py:133:        self.position_size = position_size
strategy/event_backtest.py:139:        self.regime_filter = regime_filter
strategy/event_backtest.py:140:        self.regime_graduated = regime_graduated
strategy/event_backtest.py:141:        self.regime_floor = regime_floor
strategy/event_backtest.py:160:        self.regime_deleverage = regime_deleverage
strategy/event_backtest.py:163:        self.breadth_regime = breadth_regime
strategy/event_backtest.py:169:        self.macro_regime = macro_regime
strategy/event_backtest.py:195:    def _compute_regime_scale(self, i, dates, close_df, market_close, market_ma60, market_ma20):
strategy/event_backtest.py:196:        """計算第 i 根 bar 進場時的 (regime_ok, regime_scale)，全部使用 t-1 資料避免 lookahead。
strategy/event_backtest.py:201:        regime_ok = True
strategy/event_backtest.py:202:        regime_scale = 1.0  # 曝險縮放（graduated mode）
strategy/event_backtest.py:212:                        if self.regime_graduated:
strategy/event_backtest.py:217:                                regime_scale = 1.0   # 強多頭：全力進場
strategy/event_backtest.py:219:                                regime_scale = 0.7   # 轉弱警告：縮減 30%
strategy/event_backtest.py:221:                                regime_scale = 0.4   # 初步轉強：保守進場
strategy/event_backtest.py:223:                                if self.regime_floor > 0:
strategy/event_backtest.py:224:                                    regime_scale = self.regime_floor
strategy/event_backtest.py:226:                                    regime_scale = 0.0
strategy/event_backtest.py:227:                                    regime_ok = False
strategy/event_backtest.py:230:                            regime_ok = mkt_val > mkt_ma60
strategy/event_backtest.py:234:        # === Breadth-aware Regime：用 universe 內部狀態修正 regime ===
strategy/event_backtest.py:235:        if self.breadth_regime and regime_ok and i >= 21 and self._ma20_all is not None:
strategy/event_backtest.py:247:                    regime_scale = min(regime_scale, 0.3)
strategy/event_backtest.py:249:                    regime_scale = min(regime_scale, 0.5)
strategy/event_backtest.py:254:        if self.macro_regime and self._vix_series is not None and regime_ok:
strategy/event_backtest.py:261:                        regime_scale *= 0.3   # 極端恐慌
strategy/event_backtest.py:263:                        regime_scale *= 0.5   # 高度緊張
strategy/event_backtest.py:265:                        regime_scale *= 0.7   # 警戒
strategy/event_backtest.py:269:        return regime_ok, regime_scale
strategy/event_backtest.py:271:    def _effective_gap_limit(self, regime_scale):
strategy/event_backtest.py:275:            if regime_scale >= 1.0:
strategy/event_backtest.py:277:            elif regime_scale >= 0.7:
strategy/event_backtest.py:284:        下一場進場（未來 bar i=N）的 regime 由 i-1=最後一根 bar（latest_date）的
strategy/event_backtest.py:291:        _, regime_scale = self._compute_regime_scale(
strategy/event_backtest.py:294:        return self._effective_gap_limit(regime_scale)
strategy/event_backtest.py:323:            大盤指數收盤價（0050），用於 regime filter
strategy/event_backtest.py:331:        equity_df : pd.DataFrame
strategy/event_backtest.py:339:        if self.regime_filter:
strategy/event_backtest.py:360:        # 存 universe_mask 供 breadth regime 使用
strategy/event_backtest.py:363:        self._ma20_all = close_df.rolling(20).mean() if self.breadth_regime else None
strategy/event_backtest.py:367:        if self.macro_regime:
strategy/event_backtest.py:404:        # 大盤 60MA（regime filter）
strategy/event_backtest.py:405:        if self.regime_filter and market_close is not None:
strategy/event_backtest.py:411:        # 保存供 next-session regime 查詢（paper trading 對齊 gap filter）
strategy/event_backtest.py:424:        capital = self.initial_capital
strategy/event_backtest.py:425:        equity_curve = []
strategy/event_backtest.py:429:        max_positions = int(1.0 / self.position_size)  # 最多同時持有
strategy/event_backtest.py:472:        peak_equity = self.initial_capital
strategy/event_backtest.py:476:        regime_below_count = 0   # 大盤連續低於 60MA 的天數
strategy/event_backtest.py:565:                    revenue = trade['shares'] * exit_price_with_slippage * (1 - self.sell_cost)
strategy/event_backtest.py:566:                    capital += revenue
strategy/event_backtest.py:605:            # ── Step 2: 計算當前總權益（用於 equity-based sizing） ──
strategy/event_backtest.py:606:            current_equity = capital
strategy/event_backtest.py:610:                    current_equity += trade['shares'] * close_val
strategy/event_backtest.py:613:            peak_equity = max(peak_equity, current_equity)
strategy/event_backtest.py:614:            current_dd = (current_equity - peak_equity) / peak_equity
strategy/event_backtest.py:626:            if self.regime_deleverage and market_ma60 is not None and active_trades:
strategy/event_backtest.py:635:                                regime_below_count += 1
strategy/event_backtest.py:637:                                regime_below_count = 0
strategy/event_backtest.py:640:                            if regime_below_count >= 2:
strategy/event_backtest.py:650:                                        revenue = trade['shares'] * exit_price_dv * (1 - self.sell_cost)
strategy/event_backtest.py:651:                                        capital += revenue
strategy/event_backtest.py:681:                regime_ok, regime_scale = self._compute_regime_scale(
strategy/event_backtest.py:685:                if regime_ok:
strategy/event_backtest.py:717:                                # Dynamic gap filter: 強勢 regime 放寬到 2.0 ATR
strategy/event_backtest.py:718:                                eff_gap_limit = self._effective_gap_limit(regime_scale)
strategy/event_backtest.py:732:                elif self.mean_reversion and not regime_ok:
strategy/event_backtest.py:774:                                    hedge_pnl = current_equity * 0.10 * hedge_return
strategy/event_backtest.py:775:                                    capital += hedge_pnl
strategy/event_backtest.py:786:                # Dynamic sector cap: regime 越弱限制越緊
strategy/event_backtest.py:788:                    if regime_scale <= 0.4:
strategy/event_backtest.py:790:                    elif regime_scale <= 0.7:
strategy/event_backtest.py:832:                                regime_scale = min(regime_scale, 0.4)
strategy/event_backtest.py:834:                                regime_scale = min(regime_scale, 0.6)
strategy/event_backtest.py:847:                            regime_scale = min(regime_scale, 0.6)
strategy/event_backtest.py:849:                            regime_scale = min(regime_scale, 0.75)
strategy/event_backtest.py:854:                # Dynamic Top-K: 弱勢 regime 自動降低持股數
strategy/event_backtest.py:856:                    if regime_scale <= 0.3:
strategy/event_backtest.py:858:                    elif regime_scale <= 0.5:
strategy/event_backtest.py:860:                    elif regime_scale <= 0.7:
strategy/event_backtest.py:927:                # Dynamic correlation filter: 強勢 regime 放寬閾值
strategy/event_backtest.py:930:                    if regime_scale >= 1.0:
strategy/event_backtest.py:932:                    elif regime_scale >= 0.7:
strategy/event_backtest.py:972:                            heat += t_trade['shares'] * risk_per_share
strategy/event_backtest.py:973:                        heat_pct = heat / current_equity if current_equity > 0 else 0
strategy/event_backtest.py:999:                    # === 動態風險預算：根據近期 realized vol 調整 position size ===
strategy/event_backtest.py:1008:                    effective_pos_size = self.position_size * rank_weight * regime_scale * gap_scale * batch_scale
strategy/event_backtest.py:1018:                                    effective_pos_size = effective_pos_size * vol_scalar
strategy/event_backtest.py:1026:                            target_risk = current_equity * effective_pos_size
strategy/event_backtest.py:1028:                            shares = target_risk / risk_per_share if risk_per_share > 0 else 0
strategy/event_backtest.py:1029:                            trade_amount = shares * actual_entry
strategy/event_backtest.py:1031:                            trade_amount = current_equity * effective_pos_size
strategy/event_backtest.py:1033:                        trade_amount = current_equity * effective_pos_size
strategy/event_backtest.py:1037:                    if capital >= actual_cost:
strategy/event_backtest.py:1038:                        shares = trade_amount / actual_entry
strategy/event_backtest.py:1039:                        capital -= actual_cost
strategy/event_backtest.py:1056:                            'shares': shares,
strategy/event_backtest.py:1072:            today_equity = capital
strategy/event_backtest.py:1076:                    today_equity += trade['shares'] * close_val
strategy/event_backtest.py:1078:            equity_curve.append({'Date': date, 'Equity': today_equity})
strategy/event_backtest.py:1080:        equity_df = pd.DataFrame(equity_curve).set_index('Date')
strategy/event_backtest.py:1098:        return trades_df, equity_df

exec
/bin/bash -lc "nl -ba strategy/event_backtest.py | sed -n '88,180p'; nl -ba strategy/event_backtest.py | sed -n '418,445p'; nl -ba strategy/event_backtest.py | sed -n '665,735p'; nl -ba strategy/event_backtest.py | sed -n '980,1065p'; nl -ba paper_tracker.py | sed -n '1,380p'; rg -n \"position-size|regime-filter|regime-graduated|reserve\" ai_report.py paper_tracker.py" in /root/work/tw_stocker
 succeeded in 0ms:
    88	        買進手續費率（預設 0.001425 = 0.1425%）
    89	    sell_cost : float
    90	        賣出成本率（手續費 + 證交稅，預設 0.004425 = 0.1425% + 0.3%）
    91	    """
    92	
    93	    def __init__(self, tp_pct=0.15, sl_pct=0.08, max_hold_days=20,
    94	                 initial_capital=1_000_000, position_size=0.10,
    95	                 tp_sl_mode='atr', tp_atr_mult=4.0, sl_atr_mult=3.0,
    96	                 trailing_stop=False, trailing_atr_mult=2.0,
    97	                 regime_filter=False, regime_graduated=False,
    98	                 regime_floor=0.30,
    99	                 gap_filter_atr=1.5,
   100	                 volume_confirm=False,
   101	                 blacklist_lookback=0, blacklist_min_wr=0.25,
   102	                 breakeven_pct=0, slippage=0, vol_parity=False,
   103	                 mean_reversion=False, dynamic_risk=False,
   104	                 futures_hedge=False,
   105	                 dd_pause_pct=0.10, dd_pause_days=5,
   106	                 consec_loss_limit=3, consec_loss_pause=5,
   107	                 sector_max_pct=0.75,
   108	                 corr_filter=0,
   109	                 max_portfolio_heat=1.0,
   110	                 rank_weighted=False,
   111	                 regime_deleverage=False,
   112	                 confidence_k=False,
   113	                 mid_hold_review=False,
   114	                 breadth_regime=False,
   115	                 candidate_breadth=False,
   116	                 theme_breadth=False,
   117	                 dynamic_sector_cap=False,
   118	                 gap_aware_sizing=False,
   119	                 cluster_penalty=False,
   120	                 macro_regime=False,
   121	                 batch_entry=1,
   122	                 dynamic_topk=False,
   123	                 dynamic_gap_filter=False,
   124	                 dynamic_corr_filter=False,
   125	                 sector_flow_tilt=False,
   126	                 tilt_strength=1.0,
   127	                 tilt_windows=None,
   128	                 buy_cost=0.001425, sell_cost=0.004425):
   129	        self.tp_pct = tp_pct
   130	        self.sl_pct = sl_pct
   131	        self.max_hold_days = max_hold_days
   132	        self.initial_capital = initial_capital
   133	        self.position_size = position_size
   134	        self.tp_sl_mode = tp_sl_mode
   135	        self.tp_atr_mult = tp_atr_mult
   136	        self.sl_atr_mult = sl_atr_mult
   137	        self.trailing_stop = trailing_stop
   138	        self.trailing_atr_mult = trailing_atr_mult
   139	        self.regime_filter = regime_filter
   140	        self.regime_graduated = regime_graduated
   141	        self.regime_floor = regime_floor
   142	        self.gap_filter_atr = gap_filter_atr
   143	        self.volume_confirm = volume_confirm
   144	        self.blacklist_lookback = blacklist_lookback
   145	        self.blacklist_min_wr = blacklist_min_wr
   146	        self.breakeven_pct = breakeven_pct
   147	        self.slippage = slippage
   148	        self.vol_parity = vol_parity
   149	        self.mean_reversion = mean_reversion
   150	        self.dynamic_risk = dynamic_risk
   151	        self.futures_hedge = futures_hedge
   152	        self.dd_pause_pct = dd_pause_pct
   153	        self.dd_pause_days = dd_pause_days
   154	        self.consec_loss_limit = consec_loss_limit
   155	        self.consec_loss_pause = consec_loss_pause
   156	        self.sector_max_pct = sector_max_pct
   157	        self.corr_filter = corr_filter
   158	        self.max_portfolio_heat = max_portfolio_heat
   159	        self.rank_weighted = rank_weighted
   160	        self.regime_deleverage = regime_deleverage
   161	        self.confidence_k = confidence_k
   162	        self.mid_hold_review = mid_hold_review
   163	        self.breadth_regime = breadth_regime
   164	        self.candidate_breadth = candidate_breadth
   165	        self.theme_breadth = theme_breadth
   166	        self.dynamic_sector_cap = dynamic_sector_cap
   167	        self.gap_aware_sizing = gap_aware_sizing
   168	        self.cluster_penalty = cluster_penalty
   169	        self.macro_regime = macro_regime
   170	        self.batch_entry = batch_entry
   171	        self.dynamic_topk = dynamic_topk
   172	        self.dynamic_gap_filter = dynamic_gap_filter
   173	        self.dynamic_corr_filter = dynamic_corr_filter
   174	        self.sector_flow_tilt = sector_flow_tilt
   175	        self.tilt_strength = tilt_strength
   176	        self.tilt_windows = tilt_windows if tilt_windows else [10, 15, 20]
   177	        self.buy_cost = buy_cost
   178	        self.sell_cost = sell_cost
   179	
   180	    def _compute_atr(self, high_df, low_df, close_df, period=20):
   418	        if self.volume_confirm and vol_df is not None:
   419	            vol_ma20 = vol_df.rolling(20).mean()
   420	        else:
   421	            vol_ma20 = None
   422	
   423	        trades = []
   424	        capital = self.initial_capital
   425	        equity_curve = []
   426	        dates = close_df.index
   427	        self._dates = dates
   428	        active_trades = {}  # ticker -> trade_info
   429	        max_positions = int(1.0 / self.position_size)  # 最多同時持有
   430	        ticker_history = {}  # ticker -> list of recent Return_Pct (for blacklist)
   431	
   432	        def is_tradable_bar(ticker, idx):
   433	            """True only when the raw OHLCV bar can support a real fill."""
   434	            required = (open_df, high_df, low_df, close_df)
   435	            if any(ticker not in df.columns for df in required):
   436	                return False
   437	            vals = [
   438	                open_df[ticker].iloc[idx],
   439	                high_df[ticker].iloc[idx],
   440	                low_df[ticker].iloc[idx],
   441	                close_df[ticker].iloc[idx],
   442	            ]
   443	            if any(pd.isna(v) or v <= 0 for v in vals):
   444	                return False
   445	            if vol_df is not None and ticker in vol_df.columns:
   665	                                        })
   666	                                        delev_tickers.append(ticker)
   667	                                        if ticker not in ticker_history:
   668	                                            ticker_history[ticker] = []
   669	                                        ticker_history[ticker].append(profit_pct)
   670	                                for t in delev_tickers:
   671	                                    del active_trades[t]
   672	                except Exception:
   673	                    pass
   674	
   675	            # ── Step 3: 處理今日進場（根據昨日收盤信號，今日 open 進場） ──
   676	            entry_allowed = (dd_pause_counter <= 0 and cl_pause_counter <= 0)
   677	
   678	            if len(active_trades) < max_positions and entry_allowed:
   679	                # ── Regime Filter + Graduated Exposure ──
   680	                # ━━ FIX: 使用 t-1 大盤數據（避免同日 lookahead） ━━
   681	                regime_ok, regime_scale = self._compute_regime_scale(
   682	                    i, dates, close_df, market_close, market_ma60, market_ma20)
   683	
   684	                candidates = []
   685	                if regime_ok:
   686	                    # ── 動量策略（正常模式） ──
   687	                    for ticker in close_df.columns:
   688	                        if ticker in active_trades:
   689	                            continue
   690	
   691	                        if self.blacklist_lookback > 0 and ticker in ticker_history:
   692	                            recent = ticker_history[ticker][-self.blacklist_lookback:]
   693	                            if len(recent) >= self.blacklist_lookback:
   694	                                wr = sum(1 for r in recent if r > 0) / len(recent)
   695	                                if wr < self.blacklist_min_wr:
   696	                                    continue
   697	
   698	                        score = total_score[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   699	                        ma = ma_60[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   700	                        prev_close = close_df[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   701	                        entry_price = open_df[ticker].iloc[i]
   702	
   703	                        if not is_tradable_bar(ticker, i):
   704	                            continue
   705	                        if pd.isna(entry_price) or pd.isna(score) or pd.isna(ma):
   706	                            continue
   707	                        if pd.isna(prev_close) or entry_price <= 0:
   708	                            continue
   709	
   710	                        if not (score >= threshold and prev_close > ma):
   711	                            continue
   712	
   713	                        if self.gap_filter_atr > 0 and atr is not None:
   714	                            atr_val = atr[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   715	                            if not pd.isna(atr_val) and atr_val > 0:
   716	                                gap = abs(entry_price - prev_close)
   717	                                # Dynamic gap filter: 強勢 regime 放寬到 2.0 ATR
   718	                                eff_gap_limit = self._effective_gap_limit(regime_scale)
   719	                                if gap > eff_gap_limit * atr_val:
   720	                                    continue
   721	
   722	                        # ━━ FIX: 使用 t-1 成交量（避免同日 lookahead——開盤時不知道今天總量） ━━
   723	                        if vol_ma20 is not None and ticker in vol_df.columns:
   724	                            prev_vol = vol_df[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   725	                            avg_vol = vol_ma20[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   726	                            if not pd.isna(prev_vol) and not pd.isna(avg_vol) and avg_vol > 0:
   727	                                if prev_vol < avg_vol:
   728	                                    continue
   729	
   730	                        candidates.append((ticker, score, entry_price))
   731	
   732	                elif self.mean_reversion and not regime_ok:
   733	                    # ── 均值回歸子策略（熊市模式） ──
   734	                    # 大盤 < 60MA 時，找超跌反彈股：RSI<30 且 5 日跌幅 > 10%
   735	                    for ticker in close_df.columns:
   980	                    # === Gap-aware sizing：跳空越大，倉位越小 ===
   981	                    gap_scale = 1.0
   982	                    if self.gap_aware_sizing and atr is not None:
   983	                        prev_close_val = close_df[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   984	                        atr_val_gap = atr[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   985	                        if not pd.isna(prev_close_val) and not pd.isna(atr_val_gap) and atr_val_gap > 0:
   986	                            gap_atr = abs(entry_price - prev_close_val) / atr_val_gap
   987	                            if gap_atr >= 1.0:
   988	                                gap_scale = 0.5   # 大跳空：半倉
   989	                            elif gap_atr >= 0.5:
   990	                                gap_scale = 0.75  # 中跳空：3/4 倉
   991	
   992	                    # === 排名加權 sizing ===
   993	                    rank_weight = 1.0
   994	                    if self.rank_weighted and len(selected) > 1:
   995	                        raw_weights = [1.4 - 0.2 * j for j in range(len(selected))]
   996	                        total_w = sum(raw_weights)
   997	                        rank_weight = raw_weights[rank_idx] / total_w * len(selected)
   998	
   999	                    # === 動態風險預算：根據近期 realized vol 調整 position size ===
  1000	                    # Batch entry is intentionally disabled at the CLI until
  1001	                    # pending-order execution is modeled end to end.
  1002	                    batch_scale = 1.0
  1003	                    if self.batch_entry > 1:
  1004	                        # 剩餘批次由後續幾天的 pending_batches 自動追蹤
  1005	                        weights = {2: [0.55, 0.45], 3: [0.45, 0.30, 0.25]}
  1006	                        batch_scale = weights.get(self.batch_entry, [1.0/self.batch_entry]*self.batch_entry)[0]
  1007	
  1008	                    effective_pos_size = self.position_size * rank_weight * regime_scale * gap_scale * batch_scale
  1009	                    if self.dynamic_risk and market_daily_ret is not None:
  1010	                        try:
  1011	                            prev_date = dates[i - 1]
  1012	                            mkt_idx = market_close.index.get_indexer([prev_date], method='ffill')[0]
  1013	                            if mkt_idx >= 20:
  1014	                                recent_vol = market_daily_ret.iloc[mkt_idx-20:mkt_idx].std()
  1015	                                target_vol = 0.01  # 目標日波動 1%
  1016	                                if not pd.isna(recent_vol) and recent_vol > 0:
  1017	                                    vol_scalar = min(2.0, max(0.3, target_vol / recent_vol))
  1018	                                    effective_pos_size = effective_pos_size * vol_scalar
  1019	                        except Exception:
  1020	                            pass
  1021	
  1022	                    # Volatility Parity 或 固定/動態比例 sizing
  1023	                    if self.vol_parity and atr is not None:
  1024	                        atr_val_sizing = atr[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
  1025	                        if not pd.isna(atr_val_sizing) and atr_val_sizing > 0:
  1026	                            target_risk = current_equity * effective_pos_size
  1027	                            risk_per_share = atr_val_sizing * self.sl_atr_mult
  1028	                            shares = target_risk / risk_per_share if risk_per_share > 0 else 0
  1029	                            trade_amount = shares * actual_entry
  1030	                        else:
  1031	                            trade_amount = current_equity * effective_pos_size
  1032	                    else:
  1033	                        trade_amount = current_equity * effective_pos_size
  1034	
  1035	                    actual_cost = trade_amount * (1 + self.buy_cost)  # 含買入手續費
  1036	
  1037	                    if capital >= actual_cost:
  1038	                        shares = trade_amount / actual_entry
  1039	                        capital -= actual_cost
  1040	
  1041	                        # 計算 TP/SL 價格（基於實際進場價含滑價）
  1042	                        if self.tp_sl_mode == 'atr' and atr is not None:
  1043	                            atr_val = atr[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
  1044	                            if pd.isna(atr_val) or atr_val <= 0:
  1045	                                # fallback 到固定百分比
  1046	                                tp_price = actual_entry * (1 + self.tp_pct)
  1047	                                sl_price = actual_entry * (1 - self.sl_pct)
  1048	                            else:
  1049	                                tp_price = actual_entry + atr_val * self.tp_atr_mult
  1050	                                sl_price = actual_entry - atr_val * self.sl_atr_mult
  1051	                        else:
  1052	                            tp_price = actual_entry * (1 + self.tp_pct)
  1053	                            sl_price = actual_entry * (1 - self.sl_pct)
  1054	
  1055	                        active_trades[ticker] = {
  1056	                            'shares': shares,
  1057	                            'entry_price': actual_entry,
  1058	                            'entry_date': date,
  1059	                            'tp_price': tp_price,
  1060	                            'sl_price': sl_price,
  1061	                            'initial_sl_price': sl_price,
  1062	                            'highest_since_entry': actual_entry,
  1063	                            'breakeven_activated': False,
  1064	                            'atr_at_entry': atr_val if (self.tp_sl_mode == 'atr'
  1065	                                                        and atr is not None
     1	#!/usr/bin/env python3
     2	"""
     3	Paper Trading 自動追蹤器 v8.5
     4	
     5	每日收盤後執行，自動模擬 v8.5 策略的實盤績效：
     6	1. 從 stock_report.html 擷取今日信號
     7	2. 追蹤已持倉的 TP/SL/時間到期
     8	3. 累積權益曲線到 paper_equity.json
     9	4. 產出 paper_trading.html 績效網頁
    10	
    11	使用方式:
    12	  python paper_tracker.py              # 每日更新（GitHub Actions 自動執行）
    13	  python paper_tracker.py --reset      # 清除所有記錄重新開始
    14	"""
    15	
    16	import json
    17	import glob
    18	import os
    19	import re
    20	import sys
    21	from datetime import datetime, date, timedelta
    22	import argparse
    23	import pandas as pd
    24	
    25	DATA_FILE = 'paper_equity.json'
    26	HTML_FILE = 'paper_trading.html'
    27	
    28	def load_data():
    29	    if os.path.exists(DATA_FILE):
    30	        with open(DATA_FILE) as f:
    31	            return json.load(f)
    32	    return {
    33	        'start_date': date.today().isoformat(),
    34	        'initial_capital': 200_000,
    35	        'capital': 200_000,
    36	        'positions': {},          # {ticker: {entry, tp, sl, entry_date, shares, day_count}}
    37	        'pending_orders': [],     # 待執行訂單（next-open 模型：訊號隔日開盤才進場）
    38	        'closed_trades': [],      # [{ticker, entry, exit, pnl_pct, reason, entry_date, exit_date}]
    39	        'equity_curve': [],       # [{date, equity, capital, n_positions}]
    40	        'daily_signals': [],      # [{date, tickers: [...]}]
    41	    }
    42	
    43	def save_data(data):
    44	    with open(DATA_FILE, 'w') as f:
    45	        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    46	
    47	def get_current_bars(tickers):
    48	    """用 yfinance 取得最新 OHLC，用於 paper fills 與 mark-to-market。"""
    49	    import yfinance as yf
    50	    bars = {}
    51	    if not tickers:
    52	        return bars
    53	
    54	    def download(symbols):
    55	        try:
    56	            return yf.download(symbols, period='5d', progress=False)
    57	        except Exception:
    58	            return None
    59	
    60	    try:
    61	        def read_bars(df, symbol_map):
    62	            if df is None or df.empty:
    63	                return {}
    64	            parsed = {}
    65	            for ticker, symbol in symbol_map.items():
    66	                bar = {
    67	                    'open': field_value(df, 'Open', symbol),
    68	                    'high': field_value(df, 'High', symbol),
    69	                    'low': field_value(df, 'Low', symbol),
    70	                    'close': field_value(df, 'Close', symbol),
    71	                }
    72	                if bar['close'] is not None:
    73	                    parsed[ticker] = bar
    74	            return parsed
    75	
    76	        def field_value(df, field, symbol):
    77	            if isinstance(df.columns, pd.MultiIndex):
    78	                if (field, symbol) not in df.columns:
    79	                    return None
    80	                series = df[(field, symbol)].dropna()
    81	            elif field in df.columns:
    82	                series = df[field].dropna()
    83	            else:
    84	                return None
    85	            if len(series) == 0:
    86	                return None
    87	            return float(series.iloc[-1])
    88	
    89	        tw_symbols = {t: f"{t}.TW" for t in tickers}
    90	        bars.update(read_bars(download(list(tw_symbols.values())), tw_symbols))
    91	        missing = [t for t in tickers if t not in bars]
    92	        if missing:
    93	            two_symbols = {t: f"{t}.TWO" for t in missing}
    94	            bars.update(read_bars(download(list(two_symbols.values())), two_symbols))
    95	    except Exception as e:
    96	        print(f"   ⚠️ 價格下載失敗: {e}")
    97	    return bars
    98	
    99	
   100	def get_current_prices(tickers):
   101	    """Backward-compatible latest close lookup."""
   102	    return {ticker: bar['close'] for ticker, bar in get_current_bars(tickers).items()}
   103	
   104	
   105	def extract_signals_from_orders():
   106	    """從 artifacts/orders_YYYYMMDD.json 擷取今日機器可讀訂單。"""
   107	    order_files = glob.glob('artifacts/orders_*.json')
   108	    if not order_files:
   109	        return []
   110	    latest = max(order_files, key=os.path.getmtime)
   111	    try:
   112	        with open(latest, encoding='utf-8') as f:
   113	            payload = json.load(f)
   114	    except Exception as e:
   115	        print(f"   ⚠️ orders JSON 讀取失敗: {e}")
   116	        return []
   117	
   118	    signals = []
   119	    for order in payload.get('orders', []):
   120	        if order.get('side') != 'buy':
   121	            continue
   122	        ref_close = order.get('reference_close')
   123	        atr = order.get('atr')
   124	        signals.append({
   125	            'ticker': order['ticker'],
   126	            'entry': float(order.get('limit_price') or order.get('reference_close')),
   127	            'tp': float(order['tp_price']),
   128	            'sl': float(order['sl_price']),
   129	            'reference_close': float(ref_close) if ref_close is not None else None,
   130	            'atr': float(atr) if atr is not None else None,
   131	            'gap_limit_atr': float(order.get('gap_limit_atr', 1.5)),
   132	            'execution_date': order.get('execution_date'),
   133	            'max_hold_days': int(order.get('max_hold_days', 20)),
   134	            'time_exit': order.get('time_exit'),
   135	        })
   136	    if signals:
   137	        print(f"   📦 使用 orders JSON: {latest}")
   138	    return signals
   139	
   140	def extract_signals_from_report():
   141	    """從 stock_report.html 擷取今日買入信號。"""
   142	    order_signals = extract_signals_from_orders()
   143	    if order_signals:
   144	        return order_signals
   145	
   146	    report_path = 'stock_report.html'
   147	    if not os.path.exists(report_path):
   148	        return []
   149	
   150	    with open(report_path) as f:
   151	        html = f.read()
   152	
   153	    # Format: <td>TICKER</td><td>SCORE</td><td>ENTRY</td><td>...建議買進...</td>
   154	    #         <td>停利: TP ... 停損: SL ...</td>
   155	    signals = []
   156	    rows = re.findall(r'<tr>(.*?)</tr>', html, re.DOTALL)
   157	    for row in rows:
   158	        if '建議買進' not in row:
   159	            continue
   160	        ticker_m = re.search(r'<td>(\d{4})</td>', row)
   161	        entry_m = re.findall(r'<td[^>]*>([\d\.]+)</td>', row)
   162	        tp_m = re.search(r'停利.*?>([\d\.]+)<', row)
   163	        sl_m = re.search(r'停損.*?>([\d\.]+)<', row)
   164	        if ticker_m and len(entry_m) >= 3 and tp_m and sl_m:
   165	            signals.append({
   166	                'ticker': ticker_m.group(1),
   167	                'entry': float(entry_m[2]),  # third number is entry price (1st=ticker, 2nd=score, 3rd=price)
   168	                'tp': float(tp_m.group(1)),
   169	                'sl': float(sl_m.group(1)),
   170	                'max_hold_days': 20,
   171	            })
   172	    return signals
   173	
   174	def update_tracker(data):
   175	    """主要更新邏輯：追蹤持倉、結算已平倉、記錄新信號。"""
   176	    today = date.today().isoformat()
   177	    buy_cost_rate = 0.001425
   178	    sell_cost_rate = 0.004425
   179	    slippage = 0.001
   180	    max_hold = 20
   181	    reserve_ratio = 0.10
   182	    reserve_cash = data['initial_capital'] * reserve_ratio
   183	
   184	    print(f"📊 Paper Tracker 更新 ({today})")
   185	    print(f"   初始資金: {data['initial_capital']:,.0f}")
   186	    print(f"   當前現金: {data['capital']:,.0f}")
   187	    print(f"   保留現金: {reserve_cash:,.0f} ({reserve_ratio:.0%} 本金)")
   188	    print(f"   持倉檔數: {len(data['positions'])}")
   189	
   190	    # 0. 避免重複執行
   191	    if data['equity_curve'] and data['equity_curve'][-1].get('date') == today:
   192	        print(f"   ⚠️ 今日已更新過，跳過")
   193	        return
   194	
   195	    # 1. 取得所有相關股票的最新價格
   196	    all_tickers = list(data['positions'].keys())
   197	    signals = extract_signals_from_report()
   198	    signal_tickers = [s['ticker'] for s in signals]
   199	    pending_orders = data.get('pending_orders', [])
   200	    pending_tickers = [o['ticker'] for o in pending_orders]
   201	    all_tickers_set = set(all_tickers + signal_tickers + pending_tickers)
   202	    bars = get_current_bars(list(all_tickers_set))
   203	    prices = {ticker: bar['close'] for ticker, bar in bars.items()}
   204	
   205	    # 2. 追蹤已持倉：檢查 TP/SL/時間到期
   206	    to_close = []
   207	    for ticker, pos in data['positions'].items():
   208	        pos['day_count'] = pos.get('day_count', 0) + 1
   209	        bar = bars.get(ticker)
   210	        if bar is None or bar.get('close') is None:
   211	            continue
   212	
   213	        reason = None
   214	        exit_price = bar['close']
   215	        pos_max_hold = pos.get('max_hold_days', max_hold)
   216	        # Conservative same-day ordering: SL before TP, matching backtest.
   217	        if bar.get('low') is not None and bar['low'] <= pos['sl']:
   218	            reason = 'SL'
   219	            open_price = bar.get('open')
   220	            exit_price = open_price if open_price is not None and open_price < pos['sl'] else pos['sl']
   221	        elif bar.get('high') is not None and bar['high'] >= pos['tp']:
   222	            reason = 'TP'
   223	            open_price = bar.get('open')
   224	            exit_price = open_price if open_price is not None and open_price > pos['tp'] else pos['tp']
   225	        elif pos['day_count'] >= pos_max_hold:
   226	            reason = 'TIME'
   227	            exit_price = bar['close']
   228	
   229	        if reason:
   230	            # 計算 PnL
   231	            sell_cost = exit_price * pos['shares'] * sell_cost_rate
   232	            slippage_cost = exit_price * pos['shares'] * slippage
   233	            proceeds = exit_price * pos['shares'] - sell_cost - slippage_cost
   234	            cost_basis = pos['entry'] * pos['shares'] * (1 + buy_cost_rate + slippage)
   235	            pnl = proceeds - cost_basis
   236	            pnl_pct = (exit_price / pos['entry'] - 1) * 100
   237	
   238	            data['capital'] += proceeds
   239	            data['closed_trades'].append({
   240	                'ticker': ticker,
   241	                'entry': pos['entry'],
   242	                'exit': exit_price,
   243	                'shares': pos['shares'],
   244	                'pnl': round(pnl, 0),
   245	                'pnl_pct': round(pnl_pct, 2),
   246	                'reason': reason,
   247	                'entry_date': pos['entry_date'],
   248	                'exit_date': today,
   249	                'days_held': pos['day_count'],
   250	            })
   251	            to_close.append(ticker)
   252	            emoji = '🟢' if pnl > 0 else '🔴'
   253	            print(f"   {emoji} 平倉 {ticker}: {pos['entry']:.1f}→{exit_price:.1f} ({pnl_pct:+.1f}%) [{reason}] 持{pos['day_count']}天")
   254	
   255	    for t in to_close:
   256	        del data['positions'][t]
   257	
   258	    # 3. 執行到期的待執行訂單（next-open 模型：訊號於前一交易日產生，今日開盤進場）
   259	    #    對齊回測引擎 event_backtest.py:659 —— entry_price = 當日開盤價（無條件），
   260	    #    再套用回測的 gap filter（開盤相對前日收盤跳空 > gap_limit×ATR 則放棄）。
   261	    due_orders = [o for o in pending_orders
   262	                  if not o.get('execution_date') or o['execution_date'] <= today]
   263	    deferred = [o for o in pending_orders
   264	                if o.get('execution_date') and o['execution_date'] > today]
   265	    opened = 0
   266	    if due_orders:
   267	        max_new = 7 - len(data['positions'])
   268	        candidates = []
   269	        for sig in due_orders:
   270	            if len(candidates) >= max_new:
   271	                break
   272	            ticker = sig['ticker']
   273	            if ticker in data['positions']:
   274	                continue
   275	            bar = bars.get(ticker)
   276	            if bar is None:
   277	                print(f"   ⏭️ 無報價 {ticker}: 待執行訂單無法成交")
   278	                continue
   279	            entry_price = bar.get('open')
   280	            if entry_price is None or entry_price <= 0:
   281	                entry_price = bar.get('close')  # 開盤價缺漏時退用收盤
   282	            if entry_price is None or entry_price <= 0:
   283	                continue
   284	            # Gap filter（對齊回測）：開盤相對前日收盤跳空過大則放棄
   285	            ref_close = sig.get('reference_close') or sig.get('entry')
   286	            atr = sig.get('atr')
   287	            gap_limit = sig.get('gap_limit_atr', 1.5)
   288	            if atr and atr > 0 and ref_close:
   289	                if abs(entry_price - ref_close) > gap_limit * atr:
   290	                    print(f"   ⏭️ 跳空過濾 {ticker}: |{entry_price:.1f}-{ref_close:.1f}| > {gap_limit:.1f}×ATR({atr:.1f})")
   291	                    continue
   292	            candidates.append((sig, entry_price))
   293	
   294	        for idx, (sig, entry_price) in enumerate(candidates):
   295	            available_cash = max(data['capital'] - reserve_cash, 0)
   296	            remaining_candidates = len(candidates) - idx
   297	            if available_cash <= 0:
   298	                print(f"   💵 保留本金 10%，可投入現金不足，停止開倉")
   299	                break
   300	
   301	            gross_budget = available_cash / remaining_candidates
   302	            trade_amount = gross_budget / (1 + buy_cost_rate + slippage)
   303	            shares = int(trade_amount / entry_price)
   304	            if shares <= 0:
   305	                print(f"   💵 資金不足 {sig['ticker']}: 無法在保留本金 10% 後買進")
   306	                continue
   307	
   308	            actual_trade_amount = shares * entry_price
   309	            buy_cost = actual_trade_amount * (buy_cost_rate + slippage)
   310	            if data['capital'] - actual_trade_amount - buy_cost < reserve_cash:
   311	                print(f"   💵 資金不足 {sig['ticker']}: 保留本金 10% 後不開倉")
   312	                continue
   313	
   314	            ticker = sig['ticker']
   315	            data['capital'] -= (actual_trade_amount + buy_cost)
   316	            data['positions'][ticker] = {
   317	                'entry': entry_price,
   318	                'tp': sig['tp'],
   319	                'sl': sig['sl'],
   320	                'entry_date': today,
   321	                'shares': shares,
   322	                'day_count': 0,
   323	                'max_hold_days': sig.get('max_hold_days', max_hold),
   324	            }
   325	            opened += 1
   326	            print(
   327	                f"   🆕 開倉 {ticker} @ {entry_price:.1f} × {shares:,.0f} "
   328	                f"(投入 {actual_trade_amount:,.0f}, TP {sig['tp']:.1f} / SL {sig['sl']:.1f})"
   329	            )
   330	        if opened:
   331	            print(f"   ✅ 今日開倉 {opened} 檔（待執行 {len(due_orders)} 筆）")
   332	        else:
   333	            print(f"   ⚠️ 待執行 {len(due_orders)} 筆皆未成交（跳空/資金/已持有）")
   334	
   335	    # 4. 記錄今日訊號，登錄為待執行訂單（隔日開盤執行，對齊回測 next-open）
   336	    if signals:
   337	        data['daily_signals'].append({'date': today, 'tickers': signal_tickers})
   338	        new_pending = [{
   339	            'ticker': s['ticker'],
   340	            'entry': s['entry'],
   341	            'tp': s['tp'],
   342	            'sl': s['sl'],
   343	            'reference_close': s.get('reference_close', s['entry']),
   344	            'atr': s.get('atr'),
   345	            'gap_limit_atr': s.get('gap_limit_atr', 1.5),
   346	            'execution_date': s.get('execution_date'),
   347	            'max_hold_days': s.get('max_hold_days', max_hold),
   348	            'signal_date': today,
   349	        } for s in signals]
   350	        # 今日訊號取代舊的待執行單（每交易日重新排序）；保留尚未到期者
   351	        data['pending_orders'] = new_pending + deferred
   352	        exec_date = signals[0].get('execution_date') or '次一交易日'
   353	        print(f"   📥 已登錄 {len(new_pending)} 筆待執行訂單（{exec_date} 開盤執行）")
   354	    else:
   355	        data['pending_orders'] = deferred
   356	        print(f"   📋 今日無信號")
   357	
   358	    # 4. 計算今日總權益
   359	    total_equity = data['capital']
   360	    for ticker, pos in data['positions'].items():
   361	        price = prices.get(ticker, pos['entry'])
   362	        total_equity += price * pos['shares']
   363	
   364	    data['equity_curve'].append({
   365	        'date': today,
   366	        'equity': round(total_equity, 0),
   367	        'capital': round(data['capital'], 0),
   368	        'n_positions': len(data['positions']),
   369	        'n_closed_today': len(to_close),
   370	    })
   371	
   372	    total_return = (total_equity / data['initial_capital'] - 1) * 100
   373	    print(f"\n   💰 總權益: {total_equity:,.0f} ({total_return:+.1f}%)")
   374	    print(f"   📈 已完成交易: {len(data['closed_trades'])} 筆")
   375	
   376	
   377	def generate_html(data):
   378	    """產出 paper trading 績效網頁。"""
   379	    today = date.today().isoformat()
   380	    initial = data['initial_capital']
paper_tracker.py:181:    reserve_ratio = 0.10
paper_tracker.py:182:    reserve_cash = data['initial_capital'] * reserve_ratio
paper_tracker.py:187:    print(f"   保留現金: {reserve_cash:,.0f} ({reserve_ratio:.0%} 本金)")
paper_tracker.py:295:            available_cash = max(data['capital'] - reserve_cash, 0)
paper_tracker.py:310:            if data['capital'] - actual_trade_amount - buy_cost < reserve_cash:
ai_report.py:1554:        '--regime-filter', action='store_true', default=True,
ai_report.py:1558:        '--no-regime-filter', action='store_false', dest='regime_filter',
ai_report.py:1604:        '--position-size', type=float, default=0.10,
ai_report.py:1688:        '--regime-graduated', action='store_true', default=True,
ai_report.py:1692:        '--no-regime-graduated', action='store_false', dest='regime_graduated',

exec
/bin/bash -lc "nl -ba strategy/event_backtest.py | sed -n '940,1045p'; nl -ba paper_tracker.py | sed -n '258,330p'; nl -ba ai_report.py | sed -n '1530,1615p'; nl -ba ai_report.py | sed -n '1670,1710p'" in /root/work/tw_stocker
 succeeded in 0ms:
   940	                        if lookback >= 10:
   941	                            sel_tickers = [s[0] for s in selected]
   942	                            all_held = list(active_trades.keys()) + sel_tickers
   943	                            ret_slice = close_df[all_held].iloc[max(0,i-lookback):i].pct_change().dropna()
   944	                            if len(ret_slice) >= 5:
   945	                                corr = ret_slice.corr()
   946	                                to_drop = set()
   947	                                for si in range(len(sel_tickers)):
   948	                                    if sel_tickers[si] in to_drop:
   949	                                        continue
   950	                                    for sj in range(si+1, len(sel_tickers)):
   951	                                        pair_corr = corr.loc[sel_tickers[si], sel_tickers[sj]] \
   952	                                            if sel_tickers[si] in corr.index and sel_tickers[sj] in corr.columns \
   953	                                            else 0
   954	                                        if pair_corr > eff_corr_filter:
   955	                                            to_drop.add(sel_tickers[sj])
   956	                                if to_drop:
   957	                                    selected = [s for s in selected if s[0] not in to_drop]
   958	                                    # 補上被過濾掉的名額
   959	                                    remaining = [c for c in candidates if c[0] not in
   960	                                                 {s[0] for s in selected} and c[0] not in to_drop]
   961	                                    selected += remaining[:min(top_k, slots_available) - len(selected)]
   962	                    except Exception:
   963	                        pass
   964	
   965	                for rank_idx, (ticker, score, entry_price) in enumerate(selected):
   966	                    # === Portfolio Heat Cap: 進場前檢查組合總風險 ===
   967	                    if self.max_portfolio_heat < 1.0 and active_trades:
   968	                        heat = 0
   969	                        for t_ticker, t_trade in active_trades.items():
   970	                            t_price = close_df[t_ticker].iloc[i] if not pd.isna(close_df[t_ticker].iloc[i]) else t_trade['entry_price']
   971	                            risk_per_share = max(0, t_price - t_trade['sl_price'])
   972	                            heat += t_trade['shares'] * risk_per_share
   973	                        heat_pct = heat / current_equity if current_equity > 0 else 0
   974	                        if heat_pct >= self.max_portfolio_heat:
   975	                            continue  # 組合熱度已滿，跳過新進場
   976	
   977	                    # 滑價模型：買入時價格略高
   978	                    actual_entry = entry_price * (1 + self.slippage)
   979	
   980	                    # === Gap-aware sizing：跳空越大，倉位越小 ===
   981	                    gap_scale = 1.0
   982	                    if self.gap_aware_sizing and atr is not None:
   983	                        prev_close_val = close_df[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   984	                        atr_val_gap = atr[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
   985	                        if not pd.isna(prev_close_val) and not pd.isna(atr_val_gap) and atr_val_gap > 0:
   986	                            gap_atr = abs(entry_price - prev_close_val) / atr_val_gap
   987	                            if gap_atr >= 1.0:
   988	                                gap_scale = 0.5   # 大跳空：半倉
   989	                            elif gap_atr >= 0.5:
   990	                                gap_scale = 0.75  # 中跳空：3/4 倉
   991	
   992	                    # === 排名加權 sizing ===
   993	                    rank_weight = 1.0
   994	                    if self.rank_weighted and len(selected) > 1:
   995	                        raw_weights = [1.4 - 0.2 * j for j in range(len(selected))]
   996	                        total_w = sum(raw_weights)
   997	                        rank_weight = raw_weights[rank_idx] / total_w * len(selected)
   998	
   999	                    # === 動態風險預算：根據近期 realized vol 調整 position size ===
  1000	                    # Batch entry is intentionally disabled at the CLI until
  1001	                    # pending-order execution is modeled end to end.
  1002	                    batch_scale = 1.0
  1003	                    if self.batch_entry > 1:
  1004	                        # 剩餘批次由後續幾天的 pending_batches 自動追蹤
  1005	                        weights = {2: [0.55, 0.45], 3: [0.45, 0.30, 0.25]}
  1006	                        batch_scale = weights.get(self.batch_entry, [1.0/self.batch_entry]*self.batch_entry)[0]
  1007	
  1008	                    effective_pos_size = self.position_size * rank_weight * regime_scale * gap_scale * batch_scale
  1009	                    if self.dynamic_risk and market_daily_ret is not None:
  1010	                        try:
  1011	                            prev_date = dates[i - 1]
  1012	                            mkt_idx = market_close.index.get_indexer([prev_date], method='ffill')[0]
  1013	                            if mkt_idx >= 20:
  1014	                                recent_vol = market_daily_ret.iloc[mkt_idx-20:mkt_idx].std()
  1015	                                target_vol = 0.01  # 目標日波動 1%
  1016	                                if not pd.isna(recent_vol) and recent_vol > 0:
  1017	                                    vol_scalar = min(2.0, max(0.3, target_vol / recent_vol))
  1018	                                    effective_pos_size = effective_pos_size * vol_scalar
  1019	                        except Exception:
  1020	                            pass
  1021	
  1022	                    # Volatility Parity 或 固定/動態比例 sizing
  1023	                    if self.vol_parity and atr is not None:
  1024	                        atr_val_sizing = atr[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
  1025	                        if not pd.isna(atr_val_sizing) and atr_val_sizing > 0:
  1026	                            target_risk = current_equity * effective_pos_size
  1027	                            risk_per_share = atr_val_sizing * self.sl_atr_mult
  1028	                            shares = target_risk / risk_per_share if risk_per_share > 0 else 0
  1029	                            trade_amount = shares * actual_entry
  1030	                        else:
  1031	                            trade_amount = current_equity * effective_pos_size
  1032	                    else:
  1033	                        trade_amount = current_equity * effective_pos_size
  1034	
  1035	                    actual_cost = trade_amount * (1 + self.buy_cost)  # 含買入手續費
  1036	
  1037	                    if capital >= actual_cost:
  1038	                        shares = trade_amount / actual_entry
  1039	                        capital -= actual_cost
  1040	
  1041	                        # 計算 TP/SL 價格（基於實際進場價含滑價）
  1042	                        if self.tp_sl_mode == 'atr' and atr is not None:
  1043	                            atr_val = atr[ticker].iloc[i - 1] if i - 1 >= 0 else np.nan
  1044	                            if pd.isna(atr_val) or atr_val <= 0:
  1045	                                # fallback 到固定百分比
   258	    # 3. 執行到期的待執行訂單（next-open 模型：訊號於前一交易日產生，今日開盤進場）
   259	    #    對齊回測引擎 event_backtest.py:659 —— entry_price = 當日開盤價（無條件），
   260	    #    再套用回測的 gap filter（開盤相對前日收盤跳空 > gap_limit×ATR 則放棄）。
   261	    due_orders = [o for o in pending_orders
   262	                  if not o.get('execution_date') or o['execution_date'] <= today]
   263	    deferred = [o for o in pending_orders
   264	                if o.get('execution_date') and o['execution_date'] > today]
   265	    opened = 0
   266	    if due_orders:
   267	        max_new = 7 - len(data['positions'])
   268	        candidates = []
   269	        for sig in due_orders:
   270	            if len(candidates) >= max_new:
   271	                break
   272	            ticker = sig['ticker']
   273	            if ticker in data['positions']:
   274	                continue
   275	            bar = bars.get(ticker)
   276	            if bar is None:
   277	                print(f"   ⏭️ 無報價 {ticker}: 待執行訂單無法成交")
   278	                continue
   279	            entry_price = bar.get('open')
   280	            if entry_price is None or entry_price <= 0:
   281	                entry_price = bar.get('close')  # 開盤價缺漏時退用收盤
   282	            if entry_price is None or entry_price <= 0:
   283	                continue
   284	            # Gap filter（對齊回測）：開盤相對前日收盤跳空過大則放棄
   285	            ref_close = sig.get('reference_close') or sig.get('entry')
   286	            atr = sig.get('atr')
   287	            gap_limit = sig.get('gap_limit_atr', 1.5)
   288	            if atr and atr > 0 and ref_close:
   289	                if abs(entry_price - ref_close) > gap_limit * atr:
   290	                    print(f"   ⏭️ 跳空過濾 {ticker}: |{entry_price:.1f}-{ref_close:.1f}| > {gap_limit:.1f}×ATR({atr:.1f})")
   291	                    continue
   292	            candidates.append((sig, entry_price))
   293	
   294	        for idx, (sig, entry_price) in enumerate(candidates):
   295	            available_cash = max(data['capital'] - reserve_cash, 0)
   296	            remaining_candidates = len(candidates) - idx
   297	            if available_cash <= 0:
   298	                print(f"   💵 保留本金 10%，可投入現金不足，停止開倉")
   299	                break
   300	
   301	            gross_budget = available_cash / remaining_candidates
   302	            trade_amount = gross_budget / (1 + buy_cost_rate + slippage)
   303	            shares = int(trade_amount / entry_price)
   304	            if shares <= 0:
   305	                print(f"   💵 資金不足 {sig['ticker']}: 無法在保留本金 10% 後買進")
   306	                continue
   307	
   308	            actual_trade_amount = shares * entry_price
   309	            buy_cost = actual_trade_amount * (buy_cost_rate + slippage)
   310	            if data['capital'] - actual_trade_amount - buy_cost < reserve_cash:
   311	                print(f"   💵 資金不足 {sig['ticker']}: 保留本金 10% 後不開倉")
   312	                continue
   313	
   314	            ticker = sig['ticker']
   315	            data['capital'] -= (actual_trade_amount + buy_cost)
   316	            data['positions'][ticker] = {
   317	                'entry': entry_price,
   318	                'tp': sig['tp'],
   319	                'sl': sig['sl'],
   320	                'entry_date': today,
   321	                'shares': shares,
   322	                'day_count': 0,
   323	                'max_hold_days': sig.get('max_hold_days', max_hold),
   324	            }
   325	            opened += 1
   326	            print(
   327	                f"   🆕 開倉 {ticker} @ {entry_price:.1f} × {shares:,.0f} "
   328	                f"(投入 {actual_trade_amount:,.0f}, TP {sig['tp']:.1f} / SL {sig['sl']:.1f})"
   329	            )
   330	        if opened:
  1530	    )
  1531	    parser.add_argument(
  1532	        '--trailing-atr', type=float, default=2.0,
  1533	        help='移動停利 ATR 倍數 (預設: 2.0, 從最高點回落此倍數 ATR 觸發)'
  1534	    )
  1535	
  1536	    # 選股
  1537	    parser.add_argument(
  1538	        '--top-k', type=int, default=7,
  1539	        help='每日最多進場股票數 (預設: 7)'
  1540	    )
  1541	    parser.add_argument(
  1542	        '--threshold', type=float, default=2.0,
  1543	        help='AI 評分安全下限 (預設: 2.0，低於此分數不進場)'
  1544	    )
  1545	
  1546	    # 持倉
  1547	    parser.add_argument(
  1548	        '--hold-days', type=int, default=20,
  1549	        help='最大持倉交易日數 (預設: 20)'
  1550	    )
  1551	
  1552	    # 進場過濾器
  1553	    parser.add_argument(
  1554	        '--regime-filter', action='store_true', default=True,
  1555	        help='大盤過濾 (0050 > 60MA 才允許進場, 預設: ON)'
  1556	    )
  1557	    parser.add_argument(
  1558	        '--no-regime-filter', action='store_false', dest='regime_filter',
  1559	        help='停用大盤過濾'
  1560	    )
  1561	    parser.add_argument(
  1562	        '--gap-filter', type=float, default=1.5,
  1563	        help='跳空過濾 ATR 倍數 (預設: 1.5, 0=停用)'
  1564	    )
  1565	    parser.add_argument(
  1566	        '--volume-confirm', action='store_true',
  1567	        help='啟用成交量確認 (進場日成交量 > 20日均量)'
  1568	    )
  1569	    parser.add_argument(
  1570	        '--blacklist', type=int, default=0,
  1571	        help='動態黑名單回顧筆數 (預設: 0=停用, 10=最近10筆勝率<25%%則排除)'
  1572	    )
  1573	    parser.add_argument(
  1574	        '--breakeven', type=float, default=0,
  1575	        help='獲利保護觸發門檻 (預設: 0=停用, 0.03=+3%%後 SL 移至成本價)'
  1576	    )
  1577	    parser.add_argument(
  1578	        '--slippage', type=float, default=0.001,
  1579	        help='滑價模型 (預設: 0.001=10bps; 0=停用)'
  1580	    )
  1581	    parser.add_argument(
  1582	        '--vol-parity', action='store_true',
  1583	        help='啟用波動率平價 (Volatility Parity) 部位調整'
  1584	    )
  1585	    parser.add_argument(
  1586	        '--multi-ma', action='store_true',
  1587	        help='啟用多均線確認 (20MA > 60MA 才允許進場)'
  1588	    )
  1589	    parser.add_argument(
  1590	        '--ma-period', type=int, default=60,
  1591	        help='主趨勢均線天數 (預設: 60)'
  1592	    )
  1593	    parser.add_argument(
  1594	        '--ml-weights', action='store_true',
  1595	        help='啟用 LightGBM 因子加權 (取代等權加總)'
  1596	    )
  1597	
  1598	    # 資金
  1599	    parser.add_argument(
  1600	        '--capital', type=float, default=200_000,
  1601	        help='初始模擬資金 (預設: 200000)'
  1602	    )
  1603	    parser.add_argument(
  1604	        '--position-size', type=float, default=0.10,
  1605	        help='每筆倉位佔當前權益比例 (預設: 0.10 = 10%%)'
  1606	    )
  1607	
  1608	    # 成本
  1609	    parser.add_argument(
  1610	        '--buy-cost', type=float, default=0.001425,
  1611	        help='買入手續費率 (預設: 0.001425 = 0.1425%%)'
  1612	    )
  1613	    parser.add_argument(
  1614	        '--sell-cost', type=float, default=0.004425,
  1615	        help='賣出成本率 (手續費+證交稅, 預設: 0.004425 = 0.1425%%+0.3%%)'
  1670	    )
  1671	    parser.add_argument(
  1672	        '--corr-filter', type=float, default=0.8,
  1673	        help='相關性過濾門檻 (預設 0.8; 0 = 停用; 去除近 20 日相關>門檻的重複持倉)'
  1674	    )
  1675	    parser.add_argument(
  1676	        '--max-heat', type=float, default=1.0,
  1677	        help='組合熱度上限 (預設 1.0 = 停用; 實測顯示 2%% 過緊導致交易數量驟減)'
  1678	    )
  1679	    parser.add_argument(
  1680	        '--rank-weight', action='store_true',
  1681	        help='啟用排名加權 sizing (預設停用; 實測顯示對動量策略有害)'
  1682	    )
  1683	    parser.add_argument(
  1684	        '--regime-delev', action='store_true',
  1685	        help='啟用 Regime 降曝險 (預設停用; 實測顯示會錯過反彈)'
  1686	    )
  1687	    parser.add_argument(
  1688	        '--regime-graduated', action='store_true', default=True,
  1689	        help='啟用四段式曝險縮放 (100%%/70%%/40%%/0%%), 取代 binary regime filter (預設: ON)'
  1690	    )
  1691	    parser.add_argument(
  1692	        '--no-regime-graduated', action='store_false', dest='regime_graduated',
  1693	        help='停用四段式曝險，改用 binary regime filter'
  1694	    )
  1695	    parser.add_argument(
  1696	        '--regime-floor', type=float, default=0.10,
  1697	        help='空頭 regime 最低曝險下限 (0.0=完全停止, 0.2=維持20%%進場能力)'
  1698	    )
  1699	    parser.add_argument(
  1700	        '--inst-flow', type=float, default=0.0,
  1701	        help='籌碼因子權重 (預設 0 = 停用; 建議先用 0 觀察，累積數據後再加權)'
  1702	    )
  1703	    parser.add_argument(
  1704	        '--confidence-k', action='store_true',
  1705	        help='啟用 Confidence-K：根據分數品質動態調整 Top-K（分數差太大時少買）'
  1706	    )
  1707	    parser.add_argument(
  1708	        '--mid-hold-review', action='store_true',
  1709	        help='啟用中期汰弱：持有 10-14 天仍虧損且動量衰退→提早出場'
  1710	    )

exec
/bin/bash -lc "nl -ba strategy/risk_metrics.py | sed -n '1,300p'; nl -ba paper_tracker.py | sed -n '377,470p'; rg -n \"profit_factor|Profit Factor|gross_profit|gross_loss|sortino|downside\" -S . -g '"'!**/.git/**'"' -g '"'!**/.hermes/**'"'" in /root/work/tw_stocker
 succeeded in 5172ms:
./paper_tracker.py:589:            <div class="label">Profit Factor</div>
./sweep.py:60:        'sortino': get(r'Sortino Ratio:\s+([\+\-\d\.]+)', 'Sortino'),
./sweep.py:65:        'pf': get(r'Profit Factor:\s+(inf|[\d\.]+)', 'profit factor'),
./sweep.py:266:        print(f"{r['name']:<24s} | {r['ann']:>+6.1f}% | {r['sharpe']:>6.3f} | {r['sortino']:>5.2f} | "
./sweep.py:324:            writer = csv.DictWriter(f, fieldnames=['name', 'args', 'ann', 'sharpe', 'sortino',
./STRATEGY_GUIDE.md:60:| **Profit Factor** | **1.95** |
./walk_forward.py:61:        'sortino': get(r'Sortino Ratio:\s+([\+\-\d\.]+)', 'Sortino'),
./walk_forward.py:66:        'pf': get(r'Profit Factor:\s+(inf|[\d\.]+)', 'profit factor'),
./test_finlab_factors.py:53:        'sortino': get(r'Sortino Ratio:\s+([\+\-\d\.]+)'),
./test_finlab_factors.py:58:        'pf': get(r'Profit Factor:\s+([\d\.]+)'),

exec
/bin/bash -lc "nl -ba ai_report.py | sed -n '1400,1485p'; nl -ba ai_report.py | sed -n '330,380p'; git diff -- stock_report.html | sed -n '1,120p'; git log --oneline --all -- artifacts/metadata_20260807.json artifacts/signals_20260807.csv stock_report.html | head -30; git blame -L 60,101 -- ai_report.py" in /root/work/tw_stocker
 succeeded in 18051ms:
^b954b2f (Voidful 2026-04-03 09:53:13 +0000  60) # ==========================================
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  61) # 預設股池：熱門權值、AI、航運、金融股（靜態池模式用）
^b954b2f (Voidful 2026-04-03 09:53:13 +0000  62) # ==========================================
^b954b2f (Voidful 2026-04-03 09:53:13 +0000  63) DEFAULT_TICKERS = [
^b954b2f (Voidful 2026-04-03 09:53:13 +0000  64)     '2330', '2317', '2454', '2308', '2881',
^b954b2f (Voidful 2026-04-03 09:53:13 +0000  65)     '2603', '3231', '3481', '2382', '2609',
^b954b2f (Voidful 2026-04-03 09:53:13 +0000  66)     '2891', '1519', '2379', '2303',
^b954b2f (Voidful 2026-04-03 09:53:13 +0000  67) ]
^b954b2f (Voidful 2026-04-03 09:53:13 +0000  68) 
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  69) # 擴展股池：全 TWSE 主要個股（動態 universe 模式用）
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  70) # 包含上市 ETF、權值股、中型股，約 200 檔候選池
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  71) EXTENDED_TICKERS = [
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  72)     # 半導體
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  73)     '2330', '2454', '2303', '3711', '2379', '6770', '3034', '2449',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  74)     '5274', '3529', '2408', '3443', '3035', '6415', '6525', '3661',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  75)     '3037', '2344', '6547',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  76)     # 電子
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  77)     '2317', '2382', '2308', '2301', '2357', '2376', '2395', '3231',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  78)     '2474', '2353', '3481', '3017', '2345', '2383', '2356', '3044',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  79)     '2327', '3036', '2324', '2377', '2385', '2360', '2404',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  80)     '2412', '2459', '2458', '3045', '6505', '3023',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  81)     '3706', '3533', '2368', '4904', '4938', '6669',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  82)     # 金融
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  83)     '2881', '2882', '2884', '2886', '2887', '2891', '2892',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  84)     '2880', '2883', '2885', '2888', '2889', '2890', '5880', '5876',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  85)     '2801', '2834', '2838', '2845', '2855', '2867', '2897',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  86)     # 傳產
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  87)     '1301', '1303', '1326', '2002', '1101', '1102', '2912',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  88)     '1216', '2207', '9904', '1402', '9910', '1605', '2603',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  89)     '2609', '2615', '1519', '2606', '6005',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  90)     # 航運/觀光
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  91)     '2618', '2610', '2605', '2634', '2637',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  92)     # 生技
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  93)     '4142', '1760', '6446', '1707', '4743',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  94)     # 其他
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  95)     '9945', '8454', '1504', '2105', '2201', '2204',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  96)     '5871', '6116', '6285', '3149', '6239',
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  97) ]
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  98) 
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800  99) # 去重
cb51e4c6 (voidful 2026-04-04 20:20:07 +0800 100) EXTENDED_TICKERS = list(dict.fromkeys(EXTENDED_TICKERS))
^b954b2f (Voidful 2026-04-03 09:53:13 +0000 101) 

codex
程式因果也已浮現：下載器只在「所有 batch 都空」時才中止；部分成功時沒有最低涵蓋率檢查，因此 116 檔請求縮成 1 檔仍會一路產報表。另因零股撮合規則屬會變動的市場制度，我會以臺灣證交所官方資料核對，避免用過時印象判斷「不可執行」。
web search: 
web search: site:twse.com.tw 盤中零股交易 撮合 開盤 9:10 1分鐘 2026 ...
exec
/bin/bash -lc "python - <<'PY'
from pathlib import Path
import pandas as pd
for p in sorted(Path('artifacts').glob('signals_*.csv')):
    d=pd.read_csv(p)
    if 'Score' not in d or len(d)==0: continue
    print(p.name, 'N=',len(d), 'mean=',round(d.Score.mean(),4), 'pass>=2=',f'{(d.Score>=2).mean():.1%}')
PY
python - <<'PY'
import itertools
for n in range(3,9):
    counts=[]
    mom=range(1,n+1)
    for trend in itertools.permutations(range(1,n+1)):
        counts.append(sum(3*m+t >= 2*n for m,t in zip(mom,trend)))
    print('N',n,'pass range',min(counts)/n,max(counts)/n,'mean count',sum(counts)/len(counts)/n)
PY" in /root/work/tw_stocker
 succeeded in 905ms:
signals_20090330.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20100129.csv N= 60 mean= 2.0333 pass>=2= 55.0%
signals_20200130.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20200629.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20201023.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20210629.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20220629.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20220818.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20221028.csv N= 60 mean= 2.0333 pass>=2= 55.0%
signals_20240611.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20240628.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20241231.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260331.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260402.csv N= 60 mean= 2.1858 pass>=2= 55.0%
signals_20260407.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20260408.csv N= 60 mean= 2.0333 pass>=2= 46.7%
signals_20260409.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260416.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20260417.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20260420.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260421.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260422.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260423.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260424.csv N= 60 mean= 2.0333 pass>=2= 53.3%
signals_20260427.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260429.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260430.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20260504.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260505.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20260506.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260507.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260508.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260511.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260512.csv N= 60 mean= 2.0333 pass>=2= 53.3%
signals_20260513.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260514.csv N= 60 mean= 2.0333 pass>=2= 48.3%
signals_20260515.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260518.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260519.csv N= 60 mean= 2.0333 pass>=2= 53.3%
signals_20260520.csv N= 60 mean= 2.0333 pass>=2= 51.7%
signals_20260521.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260522.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260525.csv N= 60 mean= 2.0333 pass>=2= 55.0%
signals_20260526.csv N= 60 mean= 2.0333 pass>=2= 50.0%
signals_20260807.csv N= 1 mean= 4.0 pass>=2= 100.0%
N 3 pass range 0.6666666666666666 1.0 mean count 0.7777777777777778
N 4 pass range 0.5 0.75 mean count 0.6875
N 5 pass range 0.6 0.8 mean count 0.6799999999999999
N 6 pass range 0.5 0.8333333333333334 mean count 0.638888888888889
N 7 pass range 0.42857142857142855 0.7142857142857143 mean count 0.6122448979591837
N 8 pass range 0.5 0.75 mean count 0.609375

exec
/bin/bash -lc "rg --files -g '"'!**/.git/**'"' -g '"'!**/.hermes/**'"' | rg '("'^|/)(test_.*'"\\.py|.*_test\\.py|pyproject\\.toml|pytest\\.ini|requirements.*\\.txt|Makefile)"'$'"' | sort" in /root/work/tw_stocker
 succeeded in 0ms:
crisis_test.py
deep_crisis_test.py
requirements.txt
test_benchmark.py
test_daily_meal_money.py
test_evaluation.py
test_experiment_registry.py
test_finlab_factors.py
test_meal_money.py
test_validation_gates.py

exec
/bin/bash -lc "python - <<'PY'
import json
import pandas as pd
from strategy.risk_metrics import compute_risk_metrics
m=json.load(open('artifacts/metadata_20260807.json'))['metrics']
e=pd.read_csv('artifacts/equity_20260807.csv',parse_dates=['Date']).set_index('Date')
t=pd.read_csv('artifacts/trades_20260807.csv')
r=compute_risk_metrics(e,t,200_000)
print('rows/signals/trades/equity:',len(pd.read_csv('artifacts/signals_20260807.csv')),len(t),len(e))
print('trade tickers:',sorted(t.Ticker.astype(str).unique()))
for k in ('total_return','ann_return','sharpe','sortino','max_drawdown_pct','total_trades'):
    print(k,'metadata=',m[k],'recomputed=',r[k])
# Compare current downside implementation against target-semideviation form.
dr=e.Equity.pct_change().dropna()
old=dr[dr<0].std()*(252**0.5)
semi=((dr.clip(upper=0).pow(2).mean())**0.5)*(252**0.5)
print('downside_vol current=',old,'semideviation=',semi)
print('sortino current=',r['sortino'],'semideviation version=',r['ann_return']/semi)
# Paper PF no-loss branch, exactly mirroring lines 392-398.
trades=[{'pnl':5000},{'pnl':2500}]
wins=[t for t in trades if t['pnl']>0]; losses=[t for t in trades if t['pnl']<=0]
total_profit=sum(t['pnl'] for t in wins) if wins else 0
total_loss=abs(sum(t['pnl'] for t in losses)) if losses else 1
print('paper PF with +5000,+2500 and no losses:', total_profit/total_loss)
PY
pytest -q" in /root/work/tw_stocker
 succeeded in 5346ms:
rows/signals/trades/equity: 1 34 738
trade tickers: ['2497']
total_return metadata= 0.0014470805842154988 recomputed= 0.0014470805842154988
ann_return metadata= 0.0004938898261037661 recomputed= 0.0004938898261037661
sharpe metadata= 0.03154146720151428 recomputed= 0.03154146720153446
sortino metadata= 0.022872433068047385 recomputed= 0.022872433068047485
max_drawdown_pct metadata= -0.03411968598801729 recomputed= -0.0341196859880174
total_trades metadata= 34 recomputed= 34
downside_vol current= 0.021593235168047085 semideviation= 0.016105289303959342
sortino current= 0.022872433068047485 semideviation version= 0.030666311966364223
paper PF with +5000,+2500 and no losses: 7500.0
....................                                                     [100%]
20 passed in 1.58s

exec
/bin/bash -lc "nl -ba ai_report.py | sed -n '380,510p'; nl -ba strategy/event_backtest.py | sed -n '190,275p'; sed -n '1,220p' artifacts/orders_20260807.json; git diff --stat; git status --short" in /root/work/tw_stocker
 succeeded in 0ms:
   380	    # 籌碼 + 新聞標注（always on）
   381	    all_tickers = [t for t, _, _ in selected] + [t for t, _, _ in not_selected[:5]]
   382	    inst_data = {}
   383	    news_data = {}
   384	    try:
   385	        inst_data = get_inst_flow_for_signals(all_tickers)
   386	    except Exception:
   387	        inst_data = {}
   388	    try:
   389	        news_data = get_news_sentiment_for_signals(all_tickers)
   390	    except Exception:
   391	        news_data = {}
   392	
   393	    # 籌碼動態 HTML section
   394	    inst_section_html = _build_inst_section()
   395	
   396	    # 顯示 Top-K 建議買進
   397	    orders = []
   398	    for rank, (ticker, score, price) in enumerate(selected, 1):
   399	        order_valid = True
   400	        order_atr = None  # 供 paper trading 複製回測 gap filter
   401	        time_exit = get_next_n_trading_days(latest_date, max_hold_days)
   402	        # 使用精確 ATR（與回測引擎同公式）
   403	        if tp_sl_mode == 'atr':
   404	            high_s = high_df[ticker] if (high_df is not None and ticker in high_df.columns) else None
   405	            low_s = low_df[ticker] if (low_df is not None and ticker in low_df.columns) else None
   406	            atr_val = _compute_display_atr(close_df[ticker], high_s, low_s)
   407	
   408	            if not pd.isna(atr_val) and atr_val > 0:
   409	                order_atr = float(atr_val)
   410	                tp_price = price + atr_val * config.get('tp_atr_mult', 3.0)
   411	                sl_price = price - atr_val * config.get('sl_atr_mult', 2.0)
   412	                # Sanity checks
   413	                if sl_price <= 0:
   414	                    sl_price = price * 0.85  # fallback: -15%
   415	                tp_pct_display = (tp_price / price - 1) * 100
   416	                sl_pct_display = (1 - sl_price / price) * 100
   417	                # 合理性檢查
   418	                if tp_pct_display > 50 or sl_pct_display > 50:
   419	                    plan = '<span style="color:#ff4444">⚠️ ATR 異常，信號無效</span>'
   420	                    order_valid = False
   421	                else:
   422	                    plan = (f'<b>停利:</b> <span style="color:#00ff00">{tp_price:.1f}</span>'
   423	                            f' (+{tp_pct_display:.1f}%) '
   424	                            f'<br><b>停損:</b> <span style="color:#ff4444">{sl_price:.1f}</span>'
   425	                            f' (-{sl_pct_display:.1f}%) '
   426	                            f'<br><b>最晚出場:</b> {time_exit}')
   427	            else:
   428	                tp_price = price * (1 + tp_pct)
   429	                sl_price = price * (1 - sl_pct)
   430	                plan = (f'<b>停利:</b> <span style="color:#00ff00">{tp_price:.1f}</span>'
   431	                        f' (+{tp_pct*100:.1f}%) '
   432	                        f'<br><b>停損:</b> <span style="color:#ff4444">{sl_price:.1f}</span>'
   433	                        f' (-{sl_pct*100:.1f}%) '
   434	                        f'<br><b>最晚出場:</b> {time_exit}')
   435	        else:
   436	            tp_price = price * (1 + tp_pct)
   437	            sl_price = price * (1 - sl_pct)
   438	            plan = (f'<b>停利:</b> <span style="color:#00ff00">{tp_price:.1f}</span>'
   439	                    f' (+{tp_pct*100:.1f}%) '
   440	                    f'<br><b>停損:</b> <span style="color:#ff4444">{sl_price:.1f}</span>'
   441	                    f' (-{sl_pct*100:.1f}%) '
   442	                    f'<br><b>最晚出場:</b> {time_exit}')
   443	
   444	        status = f'<span style="color:#00ff00; font-weight:bold;">🟢 建議買進 #{rank}</span>'
   445	
   446	        # Per-stock 歷史績效
   447	        ss = stock_stats.get(ticker, None)
   448	        if ss and ss['trades'] >= 2:
   449	            wr_color = '#00ff00' if ss['win_rate'] >= 50 else '#ff4444'
   450	            ar_color = '#00ff00' if ss['avg_return'] > 0 else '#ff4444'
   451	            hist_badge = (
   452	                f'<span style="font-size:0.72rem; color:#888;">'
   453	                f'歷史 <b>{ss["trades"]}</b>筆 | '
   454	                f'勝率 <b style="color:{wr_color}">{ss["win_rate"]:.0f}%</b> | '
   455	                f'均報酬 <b style="color:{ar_color}">{ss["avg_return"]:+.1f}%</b>'
   456	                f'</span>'
   457	            )
   458	        else:
   459	            hist_badge = '<span style="font-size:0.72rem; color:#555;">歷史資料不足</span>'
   460	
   461	        # 籌碼 + 新聞標注
   462	        idata = inst_data.get(ticker, {})
   463	        ndata = news_data.get(ticker, {})
   464	        inst_change = idata.get('change', 0.0)
   465	        inst_label = idata.get('label', '⚪ 無資料')
   466	        news_label = ndata.get('label', '⚪ 中性')
   467	        inst_color = '#00ff00' if inst_change > 2 else '#ff4444' if inst_change < -2 else '#ffab00' if abs(inst_change) > 0.5 else '#888'
   468	        inst_badge = (
   469	            f'<td><span style="font-size:0.78rem;">{inst_label}'
   470	            f'<br><span style="color:{inst_color}; font-weight:bold;">{inst_change:+.1f}%</span></span></td>'
   471	            f'<td><span style="font-size:0.78rem;">{news_label}</span></td>'
   472	        )
   473	
   474	        trading_plan_rows += (
   475	            f'<tr><td>{ticker}</td><td>{score:.2f}</td>'
   476	            f'<td>{price:.1f}</td><td>{status}</td><td>{plan}</td>'
   477	            f'<td>{hist_badge}</td>{inst_badge}</tr>\n'
   478	        )
   479	        if order_valid:
   480	            orders.append({
   481	                'signal_date': latest_date.strftime('%Y-%m-%d'),
   482	                'execution_date': get_next_n_trading_days(latest_date, 1),
   483	                'ticker': ticker,
   484	                'side': 'buy',
   485	                'rank': rank,
   486	                'score': round(float(score), 4),
   487	                'model_entry_ref': 'next_open',
   488	                'reference_close': round(float(price), 4),
   489	                'limit_price': round(float(price), 4),
   490	                'tp_price': round(float(tp_price), 4),
   491	                'sl_price': round(float(sl_price), 4),
   492	                'atr': round(order_atr, 4) if order_atr else None,
   493	                'gap_limit_atr': float(config.get('gap_filter_effective',
   494	                                                  config.get('gap_filter', 1.5))),
   495	                'max_hold_days': int(max_hold_days),
   496	                'time_exit': time_exit,
   497	                'model_version': 'v8.5',
   498	            })
   499	
   500	    # 顯示未被選入的候選（排名 > Top-K）
   501	    for ticker, score, price in not_selected[:5]:
   502	        status = '<span style="color:#ffab00">🟡 候選 (超出 Top-K)</span>'
   503	        ss = stock_stats.get(ticker, None)
   504	        hist_badge = '<span style="font-size:0.72rem; color:#555;">-</span>'
   505	        if ss and ss['trades'] >= 2:
   506	            wr_color = '#00ff00' if ss['win_rate'] >= 50 else '#ff4444'
   507	            hist_badge = f'<span style="font-size:0.72rem; color:#888;">勝率 <b style="color:{wr_color}">{ss["win_rate"]:.0f}%</b></span>'
   508	
   509	        idata = inst_data.get(ticker, {})
   510	        ndata = news_data.get(ticker, {})
   190	            true_range = pd.DataFrame(true_range, index=high_df.index, columns=high_df.columns)
   191	
   192	        atr = true_range.rolling(period).mean()
   193	        return atr
   194	
   195	    def _compute_regime_scale(self, i, dates, close_df, market_close, market_ma60, market_ma20):
   196	        """計算第 i 根 bar 進場時的 (regime_ok, regime_scale)，全部使用 t-1 資料避免 lookahead。
   197	
   198	        抽出為共用方法，讓回測迴圈與 paper trading 的 next-session 查詢
   199	        (next_session_gap_limit) 使用完全相同的邏輯，避免 drift。
   200	        """
   201	        regime_ok = True
   202	        regime_scale = 1.0  # 曝險縮放（graduated mode）
   203	        if market_ma60 is not None:
   204	            try:
   205	                prev_date = dates[i - 1]
   206	                mkt_date = market_close.index.get_indexer([prev_date], method='ffill')[0]
   207	                if mkt_date >= 0:
   208	                    mkt_val = market_close.iloc[mkt_date]
   209	                    mkt_ma60 = market_ma60.iloc[mkt_date]
   210	                    mkt_ma20 = market_ma20.iloc[mkt_date] if market_ma20 is not None else np.nan
   211	                    if not pd.isna(mkt_val) and not pd.isna(mkt_ma60):
   212	                        if self.regime_graduated:
   213	                            # 四段式曝險：100% / 70% / 40% / 0%
   214	                            above_60 = mkt_val > mkt_ma60
   215	                            above_20 = mkt_val > mkt_ma20 if not pd.isna(mkt_ma20) else above_60
   216	                            if above_60 and above_20:
   217	                                regime_scale = 1.0   # 強多頭：全力進場
   218	                            elif above_60 and not above_20:
   219	                                regime_scale = 0.7   # 轉弱警告：縮減 30%
   220	                            elif not above_60 and above_20:
   221	                                regime_scale = 0.4   # 初步轉強：保守進場
   222	                            else:
   223	                                if self.regime_floor > 0:
   224	                                    regime_scale = self.regime_floor
   225	                                else:
   226	                                    regime_scale = 0.0
   227	                                    regime_ok = False
   228	                        else:
   229	                            # 傳統 binary：大盤 > 60MA 才進場
   230	                            regime_ok = mkt_val > mkt_ma60
   231	            except Exception:
   232	                pass
   233	
   234	        # === Breadth-aware Regime：用 universe 內部狀態修正 regime ===
   235	        if self.breadth_regime and regime_ok and i >= 21 and self._ma20_all is not None:
   236	            try:
   237	                above_20ma = (close_df.iloc[i - 1] > self._ma20_all.iloc[i - 1])
   238	                if self._universe_mask is not None and i - 1 < len(self._universe_mask):
   239	                    day_univ = self._universe_mask.iloc[i - 1]
   240	                    above_20ma = above_20ma & day_univ
   241	                    total_in_univ = max(day_univ.sum(), 1)
   242	                else:
   243	                    total_in_univ = len(close_df.columns)
   244	                breadth_pct = above_20ma.sum() / total_in_univ
   245	
   246	                if breadth_pct < 0.30:
   247	                    regime_scale = min(regime_scale, 0.3)
   248	                elif breadth_pct < 0.45:
   249	                    regime_scale = min(regime_scale, 0.5)
   250	            except Exception:
   251	                pass
   252	
   253	        # === Macro Regime：VIX 宏觀壓力調節 ===
   254	        if self.macro_regime and self._vix_series is not None and regime_ok:
   255	            try:
   256	                prev_date = dates[i - 1]
   257	                vix_idx = self._vix_series.index.get_indexer([prev_date], method='ffill')[0]
   258	                if vix_idx >= 0:
   259	                    vix_val = float(self._vix_series.iloc[vix_idx])
   260	                    if vix_val > 30:
   261	                        regime_scale *= 0.3   # 極端恐慌
   262	                    elif vix_val > 25:
   263	                        regime_scale *= 0.5   # 高度緊張
   264	                    elif vix_val > 22:
   265	                        regime_scale *= 0.7   # 警戒
   266	            except Exception:
   267	                pass
   268	
   269	        return regime_ok, regime_scale
   270	
   271	    def _effective_gap_limit(self, regime_scale):
   272	        """回測進場時實際採用的 gap filter 倍數（對齊迴圈內 dynamic_gap_filter 邏輯）。"""
   273	        eff_gap_limit = self.gap_filter_atr
   274	        if self.dynamic_gap_filter:
   275	            if regime_scale >= 1.0:
{
  "orders": []
} backtest_chart.png | Bin 253552 -> 197603 bytes
 stock_report.html  | 316 ++++++++++++++++++++++++-----------------------------
 2 files changed, 144 insertions(+), 172 deletions(-)
 M backtest_chart.png
 M stock_report.html
?? REVIEW_claude_opus_20260814.md

codex
六項核對已完成；在下結論前，我會依 `superpowers:verification-before-completion` 做最後證據稽核。剛才已重算 2026-08-07 指標並跑完整測試（20 passed），下一步是確認每個判定都能對應到可點擊的檔案行號，並清楚區分「已證實」與「只能推論」。
exec
/bin/bash -lc "sed -n '1,320p' /root/.codex/plugins/cache/openai-curated-remote/superpowers/6.2.0/skills/verification-before-completion/SKILL.md" in /root/work/tw_stocker
 succeeded in 0ms:
---
name: verification-before-completion
description: Use when about to claim work is complete, fixed, or passing, before committing or creating PRs - requires running verification commands and confirming output before making any success claims; evidence before assertions always
---

# Verification Before Completion

## Overview

**Core principle:** Evidence before claims, always.

**Violating the letter of this rule is violating the spirit of this rule.**

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this message, you cannot claim it passes.

## The Gate Function

```
BEFORE claiming any status or expressing satisfaction:

1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State claim WITH evidence
5. ONLY THEN: Make the claim

Skip any step = lying, not verifying
```

## Common Failures

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Linter clean | Linter output: 0 errors | Partial check, extrapolation |
| Build succeeds | Build command: exit 0 | Linter passing, logs look good |
| Bug fixed | Test original symptom: passes | Code changed, assumed fixed |
| Regression test works | Red-green cycle verified | Test passes once |
| Agent completed | VCS diff shows changes | Agent reports "success" |
| Requirements met | Line-by-line checklist | Tests passing |

## Red Flags - STOP

- Using "should", "probably", "seems to"
- Expressing satisfaction before verification ("Great!", "Perfect!", "Done!", etc.)
- About to commit/push/PR without verification
- Trusting agent success reports
- Relying on partial verification
- Thinking "just this once"
- Tired and wanting work over
- **ANY wording implying success without having run verification**

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | RUN the verification |
| "I'm confident" | Confidence ≠ evidence |
| "Just this once" | No exceptions |
| "Linter passed" | Linter ≠ compiler |
| "Agent said success" | Verify independently |
| "I'm tired" | Exhaustion ≠ excuse |
| "Partial check is enough" | Partial proves nothing |
| "Different words so rule doesn't apply" | Spirit over letter |

## Key Patterns

**Tests:**
```
✅ [Run test command] [See: 34/34 pass] "All tests pass"
❌ "Should pass now" / "Looks correct"
```

**Regression tests (TDD Red-Green):**
```
✅ Write → Run (pass) → Revert fix → Run (MUST FAIL) → Restore → Run (pass)
❌ "I've written a regression test" (without red-green verification)
```

**Build:**
```
✅ [Run build] [See: exit 0] "Build passes"
❌ "Linter passed" (linter doesn't check compilation)
```

**Requirements:**
```
✅ Re-read plan → Create checklist → Verify each → Report gaps or completion
❌ "Tests pass, phase complete"
```

**Agent delegation:**
```
✅ Agent reports success → Check VCS diff → Verify changes → Report actual state
❌ Trust agent report
```

## When To Apply

**ALWAYS before:**
- ANY variation of success/completion claims
- ANY expression of satisfaction
- ANY positive statement about work state
- Committing, PR creation, task completion
- Moving to next task
- Delegating to agents

**Rule applies to:**
- Exact phrases
- Paraphrases and synonyms
- Implications of success
- ANY communication suggesting completion/correctness

exec
/bin/bash -lc "python - <<'PY'
import ast, json
from pathlib import Path
import pandas as pd
from strategy.risk_metrics import compute_risk_metrics

source=Path('ai_report.py').read_text()
tree=ast.parse(source)
extended=None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='EXTENDED_TICKERS' for t in node.targets) and isinstance(node.value, ast.List):
        extended=ast.literal_eval(node.value)
        break
assert extended is not None and len(dict.fromkeys(extended)) == 116
sig=pd.read_csv('artifacts/signals_20260807.csv')
tr=pd.read_csv('artifacts/trades_20260807.csv')
eq=pd.read_csv('artifacts/equity_20260807.csv',parse_dates=['Date']).set_index('Date')
meta=json.loads(Path('artifacts/metadata_20260807.json').read_text())
m=compute_risk_metrics(eq,tr,200_000)
assert len(sig)==1 and str(sig.iloc[0,0])=='2497'
assert len(tr)==34 and set(tr.Ticker.astype(str))=={'2497'}
assert abs(m['sharpe']-meta['metrics']['sharpe']) < 1e-12
assert meta['metrics']['total_trades']==34
print('evidence assertions: PASS')
print('extended_tickers=116 signals=1 unique_trade_tickers=1 trades=34 sharpe=%.8f' % m['sharpe'])
PY
pytest -q
git status --short" in /root/work/tw_stocker
 succeeded in 4422ms:
evidence assertions: PASS
extended_tickers=116 signals=1 unique_trade_tickers=1 trades=34 sharpe=0.03154147
....................                                                     [100%]
20 passed in 1.33s
 M backtest_chart.png
 M stock_report.html
?? REVIEW_claude_opus_20260814.md

逐項原始碼、artifact、公式重算與官方零股制度均已核對；未修改任何專案檔案。
  ✓ 確認專案規則、版本與工作樹狀態
  ✓ 核對 P0-1 實際 production artifacts
  ✓ 核對 P0-2 至 P1-2 原始碼與公式
  ✓ 執行針對性計算/測試並整理可信度結論
codex
# tw_stocker v8.5 交叉驗證報告

審查基準：artifact 記錄的 Git SHA `944b24fe9e3491d00cdc9a76c9fcf3e33b20c297`。本次為唯讀審查，未修改或提交檔案。

## 判定摘要

| 發現 | 判定 | 結論 |
|---|---:|---|
| P0-1 Production pipeline 靜默故障 | ✅ | 2026-08-07 確實只剩 2497、34 筆交易、Sharpe 0.03，且缺少資料涵蓋率 gate |
| P0-2 股池前視偏差 | ⚠️ | 116 檔硬編碼名單成立；存在嚴重 survivor/selection bias 風險，但無法直接證明「所有績效必然灌水」 |
| P0-3 Paper/backtest sizing 不一致 | ✅ | 回測約每筆 10% 權益再縮放；paper 把可用現金分完 |
| P0-4 零股成交模型 | ⚠️ | 回測小數股不可執行；paper 改為整數股，但仍錯用普通市場開盤價模擬零股成交 |
| P1-1 threshold=2.0 不是品質門檻 | ✅ | 本質為橫截面百分位中線，歷史快照約 47%–55% 通過 |
| P1-2 Sortino / PF | ✅／部分駁斥 | 兩者確有公式問題；但 Sortino「必然高估」不成立 |

## P0-1：Production pipeline 靜默故障 — ✅ 確認

2026-08-07 的原始產出明確顯示：

- signals 只有一列：[`2497,4.0`](</root/work/tw_stocker/artifacts/signals_20260807.csv:2>)
- trades CSV 共 34 筆，且全部是 2497；首筆見 [trades_20260807.csv](</root/work/tw_stocker/artifacts/trades_20260807.csv:2>)
- metadata 記錄 Sharpe `0.031541`、交易數 `34`：[metadata](</root/work/tw_stocker/artifacts/metadata_20260807.json:24>)
- HTML 顯示 Sharpe `0.03`、34 筆交易：[stock_report.html](</root/work/tw_stocker/stock_report.html:317)、[交易數](</root/work/tw_stocker/stock_report.html:332>)
- 報表唯一股票也是 2497：[stock_report.html](</root/work/tw_stocker/stock_report.html:373>)

「靜默故障」也有程式依據：下載器只有在所有 batch 都空時才拋錯；部分股票成功便繼續執行，沒有最低股票數或成功率檢查：[ai_strategy.py](</root/work/tw_stocker/strategy/ai_strategy.py:80>)、[唯一中止條件](</root/work/tw_stocker/strategy/ai_strategy.py:97>)、[直接回傳部分資料](</root/work/tw_stocker/strategy/ai_strategy.py:131>)。

因此 Claude 對實際數字和故障性質的判斷成立。低績效不是故障證據本身，但「116 檔請求可縮成 1 檔仍成功產報表」是明確的 production reliability defect。

## P0-2：股池前視偏差 — ⚠️ 部分確認

`EXTENDED_TICKERS` 確實是依半導體、電子、金融、傳產等類別人工列出的硬編碼名單：[ai_report.py](</root/work/tw_stocker/ai_report.py:69>)。目前實際是 **116 檔**，不是約 140 檔。

預設所謂「動態 Universe」只是在這 116 檔內，每日依過去 20 日成交額選 Top-N：[ai_report.py](</root/work/tw_stocker/ai_report.py:1816>)、[ai_strategy.py](</root/work/tw_stocker/strategy/ai_strategy.py:137>)。它並不是歷史當時完整上市櫃股票清單。

所以：

- ✅ 硬編碼、人工策展的候選池成立。
- ✅ 若用今天存續且今天認為重要的股票回測過去，會產生 survivor/selection look-ahead bias。
- ❌ 「約 140 檔」不精確，實際為 116。
- ⚠️ 「導致所有績效被系統性灌水」無法只靠原始碼證明。需要 point-in-time 上市櫃名冊、下市股票、歷史成分與 unbiased universe 重跑，才能量化方向及幅度。

這仍是足以否定目前回測可信度的重大風險。

## P0-3：Paper Trading 與回測 sizing 不一致 — ✅ 確認

回測：

- 預設每筆為當前權益 10%：[event_backtest.py](</root/work/tw_stocker/strategy/event_backtest.py:93>)、[CLI 預設](</root/work/tw_stocker/ai_report.py:1604>)
- 再乘上排名、regime、gap、batch 等縮放：[event_backtest.py](</root/work/tw_stocker/strategy/event_backtest.py:992>)
- 最後以 `current_equity × effective_pos_size` 決定投入金額：[event_backtest.py](</root/work/tw_stocker/strategy/event_backtest.py:1022>)

Paper tracker：

- 固定保留初始本金 10%：[paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:181>)
- 將其餘現金除以剩餘候選數：[paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:294>)
- 沒有使用 10% equity position size，也沒有 regime scale。

例如只有一個新候選且無既有持倉時，paper 約投入 90% 本金；回測通常只投入權益的 10%、7%、4% 或 1%。兩者風險曝險可能相差 9 至 90 倍，paper 結果不能視為該回測策略的 forward validation。

## P0-4：零股成交模型 — ⚠️ 核心問題成立，但標題過強

回測以浮點數計算股數：

```python
shares = trade_amount / actual_entry
```

見 [event_backtest.py](</root/work/tw_stocker/strategy/event_backtest.py:1035>)。小數股數確實無法直接下單。

Paper tracker 有改善：使用 `int(...)`，至少會落到整數股：[paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:301>)。因此「paper 也使用小數股」不成立。

但成交價格仍不正確：

- Paper 直接使用 yfinance 普通 OHLC 的 `open`，缺漏甚至退用收盤價：[paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:275>)
- 沒有普通交易與零股委託拆分、零股限價、排隊、部分成交或未成交模型。

證交所現行盤中零股是 9:10 才第一次撮合、採集合競價、限價委託；零股成交價也不構成普通市場開盤價。[臺灣證交所交易制度](https://www.twse.com.tw/zh/products/system/trading.html?hl=zh-TW)

所以 paper 股數「可以下單」，但用普通開盤價假定必然成交並不能代表實際零股執行。

## P1-1：threshold=2.0 不是品質門檻 — ✅ 確認

基本分數為：

```python
score = 3 × momentum百分位 + 1 × trend百分位
```

見 [ai_strategy.py](</root/work/tw_stocker/strategy/ai_strategy.py:302>)、[加權公式](</root/work/tw_stocker/strategy/ai_strategy.py:374>)。

若當日有 N 檔完整排名，每項百分位平均為 `(N+1)/(2N)`，所以分數平均確實為：

```text
4 × (N+1)/(2N) = 2 + 2/N
```

對 N=60 即 `2.0333`。我檢查現有正常 signals 快照，`score >= 2` 的通過率約為 46.7%–55.0%，通常接近一半；2026-08-07 因 N=1，唯一股票直接得到滿分 4.0。

精確地說，兩個 rank 的加權總和不保證數學上完全對稱，但 Claude 的核心結論正確：`2.0` 是相對排名中線，不是獨立於 universe 品質的絕對安全門檻。當 universe 只剩一檔時，該股票仍自動滿分，尤其清楚暴露這個問題。

## P1-2：Sortino / Profit Factor — ✅ 有問題，但需修正方向判斷

### Sortino

目前用「所有負報酬的樣本標準差」：

```python
downside_returns = daily_returns[daily_returns < 0]
downside_vol = downside_returns.std()
```

見 [risk_metrics.py](</root/work/tw_stocker/strategy/risk_metrics.py:58>)。

這不是一般 downside deviation；後者通常以最低可接受報酬為基準，對完整樣本計算負偏差平方平均。

Claude 說公式有問題是對的，但「一定使波動偏小、Sortino 系統性偏高」是錯的。以 2026-08-07 equity 實算：

- 現行 downside vol：2.159%
- 零門檻 semideviation：1.611%
- 現行 Sortino：0.0229
- semideviation 版本：0.0307

本次資料反而是現行公式低估 Sortino；偏差方向取決於負報酬頻率與分布。

### Paper Profit Factor

Paper tracker 在沒有虧損交易時把分母設成 1：

```python
total_loss = ... if losses else 1
pf = total_profit / total_loss
```

見 [paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:389>)。

因此兩筆獲利合計 7,500 元且零虧損時，畫面會顯示 PF `7500.0`，而不是 `∞`、未定義或明確的特殊狀態。這是確認的 bug。

另外，回測端 PF 也有另一問題：它加總各交易的 `Return_Pct`，不是實際損益金額：[risk_metrics.py](</root/work/tw_stocker/strategy/risk_metrics.py:83>)。由於部位大小會被 regime 與其他 scale 改變，這不等於投資組合層級 Profit Factor。

## 我的結論

**目前不可信賴用於實盤。**

理由不是單純因為最新 Sharpe 很低，而是研究、production 與 execution 三層都尚未閉環：

1. 資料下載可從 116 檔退化成 1 檔仍宣告成功。
2. 回測缺少 point-in-time、含下市股票的歷史 universe。
3. Paper sizing 與回測嚴重不一致。
4. 零股成交價格與成交機率沒有被建模。
5. Sortino、PF 等驗證指標定義不可靠。
6. 最新實際產出本身只有總報酬 0.14%、Sharpe 0.03、PF 0.93，沒有顯示可交易的正期望值。

在修正上述問題前，最多只能作為研究原型或極小額 shadow trading，不應連接真實自動下單。

驗證結果：證據斷言通過；`pytest -q` 顯示 `20 passed in 1.33s`，exit code 0。這只表示既有測試通過，並不推翻上述未被測試覆蓋的策略與執行缺陷。
tokens used
112,609
# tw_stocker v8.5 交叉驗證報告

審查基準：artifact 記錄的 Git SHA `944b24fe9e3491d00cdc9a76c9fcf3e33b20c297`。本次為唯讀審查，未修改或提交檔案。

## 判定摘要

| 發現 | 判定 | 結論 |
|---|---:|---|
| P0-1 Production pipeline 靜默故障 | ✅ | 2026-08-07 確實只剩 2497、34 筆交易、Sharpe 0.03，且缺少資料涵蓋率 gate |
| P0-2 股池前視偏差 | ⚠️ | 116 檔硬編碼名單成立；存在嚴重 survivor/selection bias 風險，但無法直接證明「所有績效必然灌水」 |
| P0-3 Paper/backtest sizing 不一致 | ✅ | 回測約每筆 10% 權益再縮放；paper 把可用現金分完 |
| P0-4 零股成交模型 | ⚠️ | 回測小數股不可執行；paper 改為整數股，但仍錯用普通市場開盤價模擬零股成交 |
| P1-1 threshold=2.0 不是品質門檻 | ✅ | 本質為橫截面百分位中線，歷史快照約 47%–55% 通過 |
| P1-2 Sortino / PF | ✅／部分駁斥 | 兩者確有公式問題；但 Sortino「必然高估」不成立 |

## P0-1：Production pipeline 靜默故障 — ✅ 確認

2026-08-07 的原始產出明確顯示：

- signals 只有一列：[`2497,4.0`](</root/work/tw_stocker/artifacts/signals_20260807.csv:2>)
- trades CSV 共 34 筆，且全部是 2497；首筆見 [trades_20260807.csv](</root/work/tw_stocker/artifacts/trades_20260807.csv:2>)
- metadata 記錄 Sharpe `0.031541`、交易數 `34`：[metadata](</root/work/tw_stocker/artifacts/metadata_20260807.json:24>)
- HTML 顯示 Sharpe `0.03`、34 筆交易：[stock_report.html](</root/work/tw_stocker/stock_report.html:317)、[交易數](</root/work/tw_stocker/stock_report.html:332>)
- 報表唯一股票也是 2497：[stock_report.html](</root/work/tw_stocker/stock_report.html:373>)

「靜默故障」也有程式依據：下載器只有在所有 batch 都空時才拋錯；部分股票成功便繼續執行，沒有最低股票數或成功率檢查：[ai_strategy.py](</root/work/tw_stocker/strategy/ai_strategy.py:80>)、[唯一中止條件](</root/work/tw_stocker/strategy/ai_strategy.py:97>)、[直接回傳部分資料](</root/work/tw_stocker/strategy/ai_strategy.py:131>)。

因此 Claude 對實際數字和故障性質的判斷成立。低績效不是故障證據本身，但「116 檔請求可縮成 1 檔仍成功產報表」是明確的 production reliability defect。

## P0-2：股池前視偏差 — ⚠️ 部分確認

`EXTENDED_TICKERS` 確實是依半導體、電子、金融、傳產等類別人工列出的硬編碼名單：[ai_report.py](</root/work/tw_stocker/ai_report.py:69>)。目前實際是 **116 檔**，不是約 140 檔。

預設所謂「動態 Universe」只是在這 116 檔內，每日依過去 20 日成交額選 Top-N：[ai_report.py](</root/work/tw_stocker/ai_report.py:1816>)、[ai_strategy.py](</root/work/tw_stocker/strategy/ai_strategy.py:137>)。它並不是歷史當時完整上市櫃股票清單。

所以：

- ✅ 硬編碼、人工策展的候選池成立。
- ✅ 若用今天存續且今天認為重要的股票回測過去，會產生 survivor/selection look-ahead bias。
- ❌ 「約 140 檔」不精確，實際為 116。
- ⚠️ 「導致所有績效被系統性灌水」無法只靠原始碼證明。需要 point-in-time 上市櫃名冊、下市股票、歷史成分與 unbiased universe 重跑，才能量化方向及幅度。

這仍是足以否定目前回測可信度的重大風險。

## P0-3：Paper Trading 與回測 sizing 不一致 — ✅ 確認

回測：

- 預設每筆為當前權益 10%：[event_backtest.py](</root/work/tw_stocker/strategy/event_backtest.py:93>)、[CLI 預設](</root/work/tw_stocker/ai_report.py:1604>)
- 再乘上排名、regime、gap、batch 等縮放：[event_backtest.py](</root/work/tw_stocker/strategy/event_backtest.py:992>)
- 最後以 `current_equity × effective_pos_size` 決定投入金額：[event_backtest.py](</root/work/tw_stocker/strategy/event_backtest.py:1022>)

Paper tracker：

- 固定保留初始本金 10%：[paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:181>)
- 將其餘現金除以剩餘候選數：[paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:294>)
- 沒有使用 10% equity position size，也沒有 regime scale。

例如只有一個新候選且無既有持倉時，paper 約投入 90% 本金；回測通常只投入權益的 10%、7%、4% 或 1%。兩者風險曝險可能相差 9 至 90 倍，paper 結果不能視為該回測策略的 forward validation。

## P0-4：零股成交模型 — ⚠️ 核心問題成立，但標題過強

回測以浮點數計算股數：

```python
shares = trade_amount / actual_entry
```

見 [event_backtest.py](</root/work/tw_stocker/strategy/event_backtest.py:1035>)。小數股數確實無法直接下單。

Paper tracker 有改善：使用 `int(...)`，至少會落到整數股：[paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:301>)。因此「paper 也使用小數股」不成立。

但成交價格仍不正確：

- Paper 直接使用 yfinance 普通 OHLC 的 `open`，缺漏甚至退用收盤價：[paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:275>)
- 沒有普通交易與零股委託拆分、零股限價、排隊、部分成交或未成交模型。

證交所現行盤中零股是 9:10 才第一次撮合、採集合競價、限價委託；零股成交價也不構成普通市場開盤價。[臺灣證交所交易制度](https://www.twse.com.tw/zh/products/system/trading.html?hl=zh-TW)

所以 paper 股數「可以下單」，但用普通開盤價假定必然成交並不能代表實際零股執行。

## P1-1：threshold=2.0 不是品質門檻 — ✅ 確認

基本分數為：

```python
score = 3 × momentum百分位 + 1 × trend百分位
```

見 [ai_strategy.py](</root/work/tw_stocker/strategy/ai_strategy.py:302>)、[加權公式](</root/work/tw_stocker/strategy/ai_strategy.py:374>)。

若當日有 N 檔完整排名，每項百分位平均為 `(N+1)/(2N)`，所以分數平均確實為：

```text
4 × (N+1)/(2N) = 2 + 2/N
```

對 N=60 即 `2.0333`。我檢查現有正常 signals 快照，`score >= 2` 的通過率約為 46.7%–55.0%，通常接近一半；2026-08-07 因 N=1，唯一股票直接得到滿分 4.0。

精確地說，兩個 rank 的加權總和不保證數學上完全對稱，但 Claude 的核心結論正確：`2.0` 是相對排名中線，不是獨立於 universe 品質的絕對安全門檻。當 universe 只剩一檔時，該股票仍自動滿分，尤其清楚暴露這個問題。

## P1-2：Sortino / Profit Factor — ✅ 有問題，但需修正方向判斷

### Sortino

目前用「所有負報酬的樣本標準差」：

```python
downside_returns = daily_returns[daily_returns < 0]
downside_vol = downside_returns.std()
```

見 [risk_metrics.py](</root/work/tw_stocker/strategy/risk_metrics.py:58>)。

這不是一般 downside deviation；後者通常以最低可接受報酬為基準，對完整樣本計算負偏差平方平均。

Claude 說公式有問題是對的，但「一定使波動偏小、Sortino 系統性偏高」是錯的。以 2026-08-07 equity 實算：

- 現行 downside vol：2.159%
- 零門檻 semideviation：1.611%
- 現行 Sortino：0.0229
- semideviation 版本：0.0307

本次資料反而是現行公式低估 Sortino；偏差方向取決於負報酬頻率與分布。

### Paper Profit Factor

Paper tracker 在沒有虧損交易時把分母設成 1：

```python
total_loss = ... if losses else 1
pf = total_profit / total_loss
```

見 [paper_tracker.py](</root/work/tw_stocker/paper_tracker.py:389>)。

因此兩筆獲利合計 7,500 元且零虧損時，畫面會顯示 PF `7500.0`，而不是 `∞`、未定義或明確的特殊狀態。這是確認的 bug。

另外，回測端 PF 也有另一問題：它加總各交易的 `Return_Pct`，不是實際損益金額：[risk_metrics.py](</root/work/tw_stocker/strategy/risk_metrics.py:83>)。由於部位大小會被 regime 與其他 scale 改變，這不等於投資組合層級 Profit Factor。

## 我的結論

**目前不可信賴用於實盤。**

理由不是單純因為最新 Sharpe 很低，而是研究、production 與 execution 三層都尚未閉環：

1. 資料下載可從 116 檔退化成 1 檔仍宣告成功。
2. 回測缺少 point-in-time、含下市股票的歷史 universe。
3. Paper sizing 與回測嚴重不一致。
4. 零股成交價格與成交機率沒有被建模。
5. Sortino、PF 等驗證指標定義不可靠。
6. 最新實際產出本身只有總報酬 0.14%、Sharpe 0.03、PF 0.93，沒有顯示可交易的正期望值。

在修正上述問題前，最多只能作為研究原型或極小額 shadow trading，不應連接真實自動下單。

驗證結果：證據斷言通過；`pytest -q` 顯示 `20 passed in 1.33s`，exit code 0。這只表示既有測試通過，並不推翻上述未被測試覆蓋的策略與執行缺陷。
