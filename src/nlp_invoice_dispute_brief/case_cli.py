import argparse
import json
from pathlib import Path

from .claims import ClaimLedger, MailClaims
from .reconciliation import DiscountDesk, PaymentDesk
from .review import ReviewDesk


def reconcile(case, records):
    """旧版严格复核：返回两行的可付、已入账、补差与原文依据。"""
    mailbox = MailClaims(case['mail'])
    discounts = DiscountDesk(records['orders'], records['receipts'], case['claims'], mailbox)
    return PaymentDesk(discounts, records['payments']).reconcile(case['allocations'])


def review(case, records, prior_allocations=None):
    """完整复核：主张台账、场景分歧、暂留、补差、折让占用与锁定冲突。"""
    ledger = ClaimLedger(case['mail'], records['orders'])
    desk = ReviewDesk(
        records['orders'], records['receipts'], case['claims'], ledger,
        payments=records['payments'], prior_allocations=prior_allocations,
    )
    return desk.review(case['allocations'], case.get('candidate_sets', []))


def main():
    parser = argparse.ArgumentParser(description='复核账单的邮件主张与折让分配')
    parser.add_argument('--case', type=Path, default=Path('fixtures/discounts.json'))
    parser.add_argument('--orders', type=Path, default=Path('fixtures/business.json'))
    parser.add_argument('--review', action='store_true', help='输出完整复核报告')
    parser.add_argument('--prior', type=Path, help='上一期已确认分配 JSON 文件')
    args = parser.parse_args()
    case = json.loads(args.case.read_text(encoding='utf-8'))
    records = json.loads(args.orders.read_text(encoding='utf-8'))
    if args.review:
        prior = json.loads(args.prior.read_text(encoding='utf-8')) if args.prior else None
        result = review(case, records, prior)
    else:
        result = reconcile(case, records)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
