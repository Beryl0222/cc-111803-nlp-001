import json
from pathlib import Path

def load_facts(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def summarize(facts):
    balances = {order['line']: order['amount_cents'] for order in facts['orders']}
    for payment in facts['payments']:
        if payment['posted']:
            balances[payment['line']] -= payment['amount_cents']
    return balances
