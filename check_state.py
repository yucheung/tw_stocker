import json
s = json.load(open('state.json'))
pending = s.get('pending_orders', [])
print(f"Pending orders: {len(pending)}")
for o in pending:
    print(f"  {o['ticker']} exec={o['execution_date']} limit={o['limit_price']}")
print(f"\nCash: {s.get('cash', 'N/A')}")
positions = s.get('positions', {})
print(f"Positions: {len(positions)}")
for t, p in positions.items():
    print(f"  {t}: {p}")
