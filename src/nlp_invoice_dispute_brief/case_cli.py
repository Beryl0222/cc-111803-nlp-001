import argparse
import json
from pathlib import Path

from .reconciliation import DiscountDesk, MailClaims, PaymentDesk


def reconcile(case, records):
    mailbox = MailClaims(case['mail'])
    discounts = DiscountDesk(records['orders'], records['receipts'], case['claims'], mailbox)
    return PaymentDesk(discounts, records['payments']).reconcile(case['allocations'])


def main():
    parser = argparse.ArgumentParser(description='复核账单的已确认折让')
    parser.add_argument('--case', type=Path, default=Path('fixtures/discounts.json'))
    parser.add_argument('--orders', type=Path, default=Path('fixtures/business.json'))
    args = parser.parse_args()
    result = reconcile(json.loads(args.case.read_text()), json.loads(args.orders.read_text()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
