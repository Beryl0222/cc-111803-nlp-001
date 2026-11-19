"""旧版严格复核入口（保持既有错误语义），新逻辑见 claims 与 review 模块。"""

from collections import defaultdict

from .claims import ClaimLedger, MailClaims

__all__ = ['MailClaims', 'ClaimLedger', 'DiscountDesk', 'PaymentDesk']


class DiscountDesk:
    def __init__(self, lines, receipts, claims, checked_mail):
        self.lines = {line['line']: line for line in lines}
        self.received = {receipt['line'] for receipt in receipts}
        if isinstance(checked_mail, ClaimLedger):
            self.ledger = checked_mail
        else:
            self.ledger = ClaimLedger(
                checked_mail.ledger.messages.values() if hasattr(checked_mail, 'ledger')
                else checked_mail.messages, lines)
        self.ledger.load(claims)

    def allocate(self, allocations):
        by_line = defaultdict(int)
        by_allowance = defaultdict(int)
        evidence = defaultdict(list)
        for allocation in allocations:
            claim = self.ledger.claims[allocation['claim']]
            line_id = allocation['line']
            line = self.lines[line_id]
            if not allocation['approved'] or line_id not in self.received:
                raise ValueError('折让分配尚未经确认或缺签收')
            if claim['invoice'] != line['invoice'] or claim['kind'] != '折让':
                raise ValueError('邮件主张与分配款项不匹配')
            cents = allocation['cents']
            if type(cents) is not int or cents <= 0:
                raise ValueError('折让金额须为正整数分')
            root = self.ledger.allowance_head(claim['id'])
            by_allowance[root] += cents
            by_line[line_id] += cents
            cap = self.ledger.claims[root].get('cents')
            if (cap is not None and by_allowance[root] > cap) or by_line[line_id] > line['amount_cents']:
                raise ValueError('分配超过邮件折让金额或订单行余额')
            evidence[line_id].append(self.ledger.quote(claim['id']))
        return by_line, evidence


class PaymentDesk:
    def __init__(self, discounts, payments):
        self.discounts = discounts
        self.posted = {payment['line']: payment for payment in payments if payment['posted']}

    def reconcile(self, allocations):
        totals, evidence = self.discounts.allocate(allocations)
        rows = []
        for line_id, line in self.discounts.lines.items():
            current = line['amount_cents'] - totals[line_id]
            payment = self.posted.get(line_id)
            posted = payment['amount_cents'] if payment else None
            rows.append({'line': line_id, 'payable': current if payment is None else 0,
                         'posted': posted, 'adjustment': current - posted if posted is not None else 0,
                         'evidence': evidence[line_id]})
        return rows
