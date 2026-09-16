import json
with open('independent_sim_data_top7/state.json') as f:
    s = json.load(f)
print('Holdings:', json.dumps(s.get('holdings', {}), ensure_ascii=False))
print('Cash:', s.get('cash', 'N/A'))
print('Last orders:', json.dumps(s.get('pending_orders', s.get('orders', []))[-5:], ensure_ascii=False, indent=2))
