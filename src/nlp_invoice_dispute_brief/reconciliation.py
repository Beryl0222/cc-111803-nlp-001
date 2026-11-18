from collections import defaultdict


class MailClaims:
    def __init__(self, messages):
        self.messages = {message['id']: message for message in messages}

    def check(self, claim):
        message = self.messages[claim['mail']]
        if message['author'] != claim['party']:
            raise ValueError('主张方不是该封信的发件人')
        start, end = claim['span']
        if not 0 <= start < end <= len(message['text']):
            raise ValueError('主张位置超出邮件')
        if any(start < right and left < end for left, right in message['forwarded']):
            raise ValueError('转发内容不能作为发件人主张')
        return message['text'][start:end]


class DiscountDesk:
    def __init__(self, lines, receipts, claims, checked_mail):
        self.lines = {line['line']: line for line in lines}
        self.received = {receipt['line'] for receipt in receipts}
        self.claims = {claim['id']: claim for claim in claims}
        self.checked_mail = checked_mail

    def allocate(self, allocations):
        by_line = defaultdict(int)
        by_claim = defaultdict(int)
        evidence = defaultdict(list)
        for allocation in allocations:
            claim = self.claims[allocation['claim']]
            line_id = allocation['line']
            line = self.lines[line_id]
            if not allocation['approved'] or line_id not in self.received:
                raise ValueError('折让分配尚未经确认或缺签收')
            if claim['invoice'] != line['invoice'] or claim['kind'] != '折让':
                raise ValueError('邮件主张与分配款项不匹配')
            cents = allocation['cents']
            if type(cents) is not int or cents <= 0:
                raise ValueError('折让金额须为正整数分')
            by_claim[claim['id']] += cents
            by_line[line_id] += cents
            if by_claim[claim['id']] > claim['cents'] or by_line[line_id] > line['amount_cents']:
                raise ValueError('分配超过邮件折让金额或订单行余额')
            evidence[line_id].append(self.checked_mail.check(claim))
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
