# TWSE Strategy C Multifactor Replay (2024–2026)

- Replay window: 2024-01-01 to 2026-09-30
- Read-only TWSE Research cache: `/root/work/TWSE_Research/data/cache`
- Reference: checked-in TWSE Research engine v3, close-price rebalances, top_n=20, bear exposure=0.2.
- Paper replay: next-session opening auction; buy limits use prior close, odd-lot shares round down, sells execute at open; commission, transaction tax, and existing simulator sell slippage apply. No TP/SL or cash yield.

## Performance

| Variant | Ending equity (TWD) | Cumulative return | Maximum drawdown |
|---|---:|---:|---:|
| Research close-fill | 3,328,844 | +232.88% | -30.87% |
| Independent sim open-fill | 1,551,003 | +55.10% | -20.32% |
| 0050 close benchmark | — | +230.02% | — |
| Open-fill minus close-fill | — | -177.78 percentage points | — |

## Monthly target and execution comparison

Target set exact matches: 33/33 (100.0%); aggregate target Jaccard: 100.0%.
Buy orders filled: 200/590; average filled buy open-vs-reference-close gap: -103.3 bps.
All MFC orders: 435 filled; 390 canceled/skipped.

| Signal date | Research targets | Port targets | Target Jaccard | Held after next open | Fills | Cancels |
|---|---:|---:|---:|---|---:|---:|
| 2024-01-11 | 20 | 20 | 100% | 2353.TW, 2371.TW, 2891.TW, 3032.TW, 3217.TWO, 3548.TWO, 3661.TW, 5269.TW, 6138.TWO, 6187.TWO, 6223.TWO, 6643.TWO, 8210.TW, 8227.TWO, 9958.TW | 15 | 5 |
| 2024-02-15 | 20 | 20 | 100% | 2891.TW, 3548.TWO, 3661.TW, 5269.TW, 6139.TW, 6187.TWO, 9958.TW | 14 | 14 |
| 2024-03-11 | 20 | 20 | 100% | 2204.TW, 2379.TW, 2454.TW, 2891.TW, 3131.TWO, 3293.TWO, 3548.TWO, 3583.TW, 5269.TW, 6187.TWO | 13 | 10 |
| 2024-04-11 | 20 | 20 | 100% | 1514.TW, 2204.TW, 2891.TW, 3036.TW, 3583.TW, 5269.TW, 5274.TWO, 6187.TWO, 6515.TW | 13 | 11 |
| 2024-05-13 | 20 | 20 | 100% | 1216.TW, 2618.TW, 2731.TW, 2883.TW, 2885.TW, 2891.TW, 3013.TW, 3363.TWO, 3406.TW, 5274.TWO | 16 | 10 |
| 2024-06-11 | 20 | 20 | 100% | 2345.TW, 2454.TW, 2615.TW, 2881.TW, 2891.TW, 3013.TW, 3406.TW, 5274.TWO, 5347.TWO, 6415.TW | 16 | 10 |
| 2024-07-11 | 20 | 20 | 100% | 2330.TW, 2368.TW, 2382.TW, 2383.TW, 2455.TW, 3008.TW, 3013.TW, 3105.TWO, 3293.TWO, 3529.TWO, 3583.TW, 3680.TWO, 6187.TWO, 6231.TWO, 6643.TWO, 6789.TW, 8086.TWO | 26 | 3 |
| 2024-08-12 | 20 | 20 | 100% | 2383.TW, 2615.TW, 3293.TWO, 3583.TW, 3680.TWO, 6187.TWO, 8069.TWO | 19 | 13 |
| 2024-09-11 | 20 | 20 | 100% | 2383.TW, 6187.TWO | 6 | 18 |
| 2024-10-11 | 20 | 20 | 100% | 1476.TW, 2330.TW, 2357.TW, 2882.TW, 3013.TW, 3293.TWO, 3363.TWO, 3583.TW, 6187.TWO, 6274.TWO, 6533.TW, 8069.TWO | 12 | 9 |
| 2024-11-11 | 20 | 20 | 100% | 2059.TW, 2317.TW, 2345.TW, 2356.TW, 2376.TW, 2382.TW, 2603.TW, 2615.TW, 2883.TW, 2885.TW, 3013.TW, 3017.TW, 3037.TW, 3231.TW, 6515.TW, 6533.TW, 6669.TW | 26 | 3 |
| 2024-12-11 | 20 | 20 | 100% | 2059.TW, 2345.TW, 2885.TW, 2891.TW, 6669.TW | 18 | 15 |
| 2025-01-13 | 20 | 20 | 100% | 1477.TW, 2377.TW, 2474.TW, 2618.TW, 3008.TW, 3081.TWO, 3363.TWO, 6446.TW, 6789.TW | 14 | 11 |
| 2025-02-11 | 20 | 20 | 100% | 2891.TW, 3363.TWO, 4979.TWO, 6215.TW, 6446.TW, 6472.TW | 11 | 16 |
| 2025-03-11 | 20 | 20 | 100% | 2303.TW, 2356.TW, 2379.TW, 2409.TW, 2474.TW, 2615.TW, 3260.TWO, 3481.TW, 8210.TW | 15 | 11 |
| 2025-04-11 | 20 | 20 | 100% | 2303.TW, 2379.TW, 2412.TW, 2615.TW, 2883.TW, 2884.TW, 8210.TW | 8 | 15 |
| 2025-05-12 | 20 | 20 | 100% | 8210.TW | 6 | 19 |
| 2025-06-11 | 20 | 20 | 100% | 2330.TW, 2345.TW, 2368.TW, 2382.TW, 2383.TW, 3017.TW, 3131.TWO, 3231.TW, 3413.TW, 3653.TW, 6187.TWO, 6274.TWO, 6805.TW, 8210.TW, 8996.TW | 15 | 5 |
| 2025-07-11 | 20 | 20 | 100% | 1560.TW, 2308.TW, 2360.TW, 2368.TW, 2891.TW, 3017.TW, 3131.TWO, 3363.TWO, 3706.TW, 5274.TWO, 6223.TWO, 6274.TWO, 6669.TW, 6805.TW, 8046.TW, 8210.TW | 24 | 4 |
| 2025-08-11 | 20 | 20 | 100% | 2308.TW, 2368.TW, 2376.TW, 3017.TW, 3706.TW, 6669.TW, 6805.TW, 8046.TW | 17 | 12 |
| 2025-09-11 | 20 | 20 | 100% | 2308.TW, 2368.TW | 8 | 18 |
| 2025-10-13 | 20 | 20 | 100% | 2308.TW | 2 | 19 |
| 2025-11-11 | 20 | 20 | 100% | 2059.TW, 2408.TW, 3324.TWO, 8299.TWO, 8996.TW | 6 | 15 |
| 2025-12-11 | 20 | 20 | 100% | 2408.TW | 4 | 19 |
| 2026-01-12 | 20 | 20 | 100% | 2408.TW, 2887.TW, 3105.TWO, 4967.TW | 4 | 16 |
| 2026-02-11 | 20 | 20 | 100% | 2408.TW, 6446.TW, 8996.TW | 6 | 17 |
| 2026-03-11 | 20 | 20 | 100% | 1815.TWO, 2308.TW, 2345.TW, 2368.TW, 2383.TW, 2404.TW, 2887.TW, 3081.TWO, 3105.TWO, 6805.TW, 8046.TW | 14 | 9 |
| 2026-04-13 | 20 | 20 | 100% | 2345.TW, 2368.TW, 2383.TW, 3081.TWO, 3105.TWO, 8046.TW | 11 | 14 |
| 2026-05-11 | 20 | 20 | 100% | 2059.TW, 2308.TW, 2345.TW, 2368.TW, 2383.TW, 6274.TWO, 8046.TW | 9 | 13 |
| 2026-06-11 | 20 | 20 | 100% | 2059.TW, 2368.TW, 2383.TW | 6 | 18 |
| 2026-07-13 | 20 | 20 | 100% | 2059.TW, 2382.TW, 2481.TW, 3167.TW, 3189.TW, 3374.TWO, 3702.TW, 3706.TW, 4916.TW, 4958.TW, 6139.TW, 6213.TW | 14 | 8 |
| 2026-08-11 | 20 | 20 | 100% | 2027.TW, 2059.TW, 2383.TW, 2637.TW, 3231.TW, 3665.TW, 3702.TW, 4931.TWO, 6213.TW, 6274.TWO | 19 | 10 |
| 2026-09-11 | 20 | 20 | 100% | 1303.TW, 1815.TWO, 2382.TW, 2455.TW, 2883.TW, 2887.TW, 2890.TW, 3006.TW, 3017.TW, 3167.TW, 3231.TW, 3324.TWO, 3374.TWO, 3443.TW, 3491.TWO, 3653.TW, 4967.TW, 5314.TWO, 6510.TWO, 8996.TW | 28 | 0 |

## Interpretation

Selection parity is evaluated before execution so an open-vs-close price difference cannot hide a factor or point-in-time bug. Filled holdings can diverge from the target set when the next open gaps above the close-based buy limit, when a slot cannot afford one share, or when sell/buy friction consumes cash. The simulator does not credit the Research engine's annual cash yield; both differences are reported model assumptions alongside the open-close price effect.
