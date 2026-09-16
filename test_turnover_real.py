#!/usr/bin/env python3
"""
驗證 paper_tracker.py 換倉 (turnover) 機制的功能測試。

只讀取 paper_equity.json（不修改），並針對 find_replace_candidate() /
get_required_rank_gap() 建立合成情境驗證 hysteresis 規則是否正確觸發。

使用方式：
  python test_turnover_real.py
"""

import json
import sys

from paper_tracker import find_replace_candidate, get_required_rank_gap, MAX_POSITIONS

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"   ✅ {name}")
    else:
        FAIL.append((name, detail))
        print(f"   ❌ {name}  {detail}")


def make_positions(specs):
    """specs: {ticker: (entry_rank, day_count)}"""
    return {
        t: {'entry_rank': rank, 'day_count': dc, 'entry': 100.0, 'shares': 10}
        for t, (rank, dc) in specs.items()
    }


def main():
    print("=" * 70)
    print("1. 讀取真實 paper_equity.json（唯讀，不寫回）")
    print("=" * 70)
    with open('paper_equity.json') as f:
        real_data = json.load(f)

    real_positions = real_data['positions']
    print(f"   目前持倉數: {len(real_positions)}/{MAX_POSITIONS}")
    for t, p in real_positions.items():
        print(f"     {t}: entry_rank={p.get('entry_rank')} day_count={p.get('day_count')}")

    # 真實資料目前只有 6 檔持倉（未額滿），驗證未額滿時 update_tracker 不會呼叫
    # find_replace_candidate（呼叫點在 len(positions) >= MAX_POSITIONS 條件內，
    # paper_tracker.py:572）。這裡直接驗證：若拿真實持倉硬跑 find_replace_candidate，
    # 函式本身仍應依規則正確判斷（因為函式本身不檢查是否額滿，那是呼叫端的責任）。
    check(
        "真實持倉數 < MAX_POSITIONS（呼叫端不會觸發換倉，符合 update_tracker:572 邏輯）",
        len(real_positions) < MAX_POSITIONS,
        f"實際 {len(real_positions)}"
    )

    print()
    print("=" * 70)
    print("2. get_required_rank_gap() 門檻驗證")
    print("=" * 70)
    gap_cases = [
        (0, None), (4, None),
        (5, 5), (7, 5),
        (8, 3), (11, 3),
        (12, 1), (100, 1),
    ]
    for day_count, expected in gap_cases:
        got = get_required_rank_gap(day_count)
        check(f"get_required_rank_gap({day_count}) == {expected}", got == expected, f"got {got}")

    print()
    print("=" * 70)
    print("3. find_replace_candidate() — 7 席全滿情境（合成資料）")
    print("=" * 70)

    # 3a. 標準情境：以真實持倉的 entry_rank 分布為基礎，補到 7 席，
    #     其中一檔 day_count=12（門檻 gap>=1），新訊號 rank 遠優於其中最弱者
    positions_7 = make_positions({
        '6213': (2, 5),   # 門檻 gap>=5
        '3653': (3, 5),   # 門檻 gap>=5
        '2408': (6, 5),   # 門檻 gap>=5, gap = 6-new
        '2301': (5, 4),   # day_count<5 → 不列入候選
        '3406': (1, 1),   # day_count<5 → 不列入候選
        '3008': (3, 12),  # 門檻 gap>=1
        '9999': (20, 12), # 門檻 gap>=1，rank 最差
    })
    result = find_replace_candidate(positions_7, new_rank=1)
    check(
        "7 席全滿 + 新訊號 rank=1 → 選出 rank 最差(20) 的 9999",
        result == '9999',
        f"got {result}"
    )

    # 3b. day_count < 5 的持倉完全被排除（即使 rank 很差）
    positions_only_young = make_positions({
        'A': (50, 4),  # rank 很差但 day_count<5 → 不可換倉
        'B': (2, 5),   # rank 好，且 gap 門檻 5：new_rank=10 時 gap=2-10=-8，不夠
    })
    result = find_replace_candidate(positions_only_young, new_rank=10)
    check(
        "全部持倉皆不足門檻（A 因 day_count<5 排除，B rank_gap 不足）→ None",
        result is None,
        f"got {result}"
    )

    # 3c. rank gap 剛好等於門檻 → 觸發；差 1 → 不觸發
    positions_edge = make_positions({'X': (10, 6)})  # day_count=6 → gap>=5
    check(
        "day_count=6, held_rank=10, new_rank=5 → gap=5 恰等於門檻 → 觸發換倉",
        find_replace_candidate(positions_edge, new_rank=5) == 'X'
    )
    check(
        "day_count=6, held_rank=10, new_rank=6 → gap=4 < 門檻 5 → 不觸發",
        find_replace_candidate(positions_edge, new_rank=6) is None
    )

    print()
    print("=" * 70)
    print("4. 邊界情境")
    print("=" * 70)

    # 4a. 新訊號 rank 比所有持倉都差（數字更大 = 排名更差）→ 不應觸發換倉
    positions_all_better = make_positions({
        'A': (1, 12), 'B': (2, 12), 'C': (3, 12),
    })
    result = find_replace_candidate(positions_all_better, new_rank=50)
    check(
        "新訊號 rank(50) 比所有持倉都差 → rank_gap 全為負 → None",
        result is None,
        f"got {result}"
    )

    # 4b. day_count < 5（最低持有期）→ 無論 rank 差距多大都不得換倉
    positions_min_hold = make_positions({'A': (100, 0), 'B': (100, 4)})
    result = find_replace_candidate(positions_min_hold, new_rank=1)
    check(
        "所有持倉 day_count<5（最低持有期）→ 無候選 → None",
        result is None,
        f"got {result}"
    )

    # 4c. new_rank is None → 直接回傳 None（不比較）
    check(
        "new_rank=None → 直接回傳 None",
        find_replace_candidate(positions_7, new_rank=None) is None
    )

    # 4d. positions 為空 → None
    check(
        "positions={} → 直接回傳 None",
        find_replace_candidate({}, new_rank=1) is None
    )

    # 4e. min_rank_gap 參數會拉高門檻（不會降低）
    positions_minrg = make_positions({'A': (10, 6)})  # 天然門檻 gap>=5
    check(
        "min_rank_gap=8 覆蓋天然門檻 5 → gap=5(10-5) 不足 8 → None",
        find_replace_candidate(positions_minrg, new_rank=5, min_rank_gap=8) is None
    )
    check(
        "min_rank_gap=3（低於天然門檻 5）不會降低門檻，仍用 5 → gap=5 觸發",
        find_replace_candidate(positions_minrg, new_rank=5, min_rank_gap=3) == 'A'
    )

    print()
    print("=" * 70)
    print("5. close_position() 呼叫語意檢查（reason='REPLACE'）— 靜態程式碼路徑核對")
    print("=" * 70)
    import inspect
    src = inspect.getsource(sys.modules['paper_tracker'])
    check(
        "update_tracker 內含 close_position(..., reason='REPLACE') 呼叫（換倉平倉）",
        "reason='REPLACE'" in src
    )
    check(
        "update_tracker 內新倉建立時寫入 entry_rank: sig.get('rank')",
        "'entry_rank': sig.get('rank')" in src
    )
    check(
        "換倉呼叫點確實在 len(data['positions']) >= MAX_POSITIONS 判斷之後",
        "if len(data['positions']) >= MAX_POSITIONS:" in src and
        src.index("if len(data['positions']) >= MAX_POSITIONS:") <
        src.index("find_replace_candidate(data['positions'], sig.get('rank'))")
    )

    print()
    print("=" * 70)
    print(f"總結: {len(PASS)} PASS / {len(FAIL)} FAIL")
    print("=" * 70)
    if FAIL:
        print("\n失敗項目:")
        for name, detail in FAIL:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    else:
        print("\n✅ 換倉機制驗證全數通過")
        sys.exit(0)


if __name__ == '__main__':
    main()

