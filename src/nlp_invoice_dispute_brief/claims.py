"""邮件主张台账。

把邮件正文与转发区间中的句子登记为结构化主张，区分当事方提出、同意与
仅仅转述三种立场，并维护折让主张在多封邮件之间的重述（同一笔折让）关系、
适用条件与批次候选。所有原文以 Unicode 字符位置登记，左端包含、右端省略。
"""

from collections import defaultdict

KINDS = ('货款', '运费', '折让')
STANCES = ('提出', '同意', '转述')


class MailClaims:
    """向后兼容的邮件主张校验入口。"""

    def __init__(self, messages):
        self.ledger = ClaimLedger(messages)

    def check(self, claim):
        message = self.ledger.messages[claim['mail']]
        if message['author'] != claim['party']:
            raise ValueError('主张方不是该封信的发件人')
        start, end = claim['span']
        if not 0 <= start < end <= len(message['text']):
            raise ValueError('主张位置超出邮件')
        if any(start < right and left < end
               for left, right in message.get('forwarded', [])):
            raise ValueError('转发内容不能作为发件人主张')
        return message['text'][start:end]


class ClaimLedger:
    def __init__(self, messages, orders=None):
        self.messages = {message['id']: message for message in messages}
        self.orders = list(orders or [])
        self.claims = {}
        self.groups = {}

    def load(self, claims):
        self.claims = {}
        for claim in claims:
            self._validate(claim)
            if claim['id'] in self.claims:
                raise ValueError('主张标识重复')
            self.claims[claim['id']] = dict(claim)
        self._check_links()
        self._build_groups()
        return self

    def _validate(self, claim):
        missing = {'id', 'mail', 'party', 'span', 'kind'} - set(claim)
        if missing:
            raise ValueError('主张缺少必填字段')
        message = self.messages.get(claim['mail'])
        if message is None:
            raise ValueError('主张邮件不存在')
        if message['author'] != claim['party']:
            raise ValueError('主张方不是该封信的发件人')
        stance = claim.get('stance', '提出')
        if stance not in STANCES:
            raise ValueError('主张立场须为提出、同意或转述')
        kind = claim['kind']
        if kind not in KINDS:
            raise ValueError('主张款项须为货款、运费或折让')
        start, end = claim['span']
        if not 0 <= start < end <= len(message['text']):
            raise ValueError('主张位置超出邮件')
        in_forward = any(start < right and left < end
                         for left, right in message.get('forwarded', []))
        if stance == '转述':
            if not in_forward:
                raise ValueError('转述主张必须位于转发区间内')
        elif in_forward:
            raise ValueError('转发内容不能作为发件人主张')
        cents = claim.get('cents')
        if cents is not None and (type(cents) is not int or cents <= 0):
            raise ValueError('主张金额须为正整数分')

    def _check_links(self):
        self.agreements = defaultdict(list)
        for claim in self.claims.values():
            for field in ('restates', 'agrees_to'):
                ref = claim.get(field)
                if ref is not None and ref not in self.claims:
                    raise ValueError('主张引用了不存在的主张')
            if claim.get('restates') and claim['kind'] != '折让':
                raise ValueError('只有折让主张可以重述同一笔折让')
            if claim.get('agrees_to'):
                self.agreements[claim['agrees_to']].append(claim['id'])

    def _build_groups(self):
        # 每个主张默认自成一组；restates 指向同笔折让的源头，沿链归并。
        groups = {claim_id: claim_id for claim_id in self.claims}

        def head(claim_id):
            while groups[claim_id] != claim_id:
                groups[claim_id] = groups[groups[claim_id]]
                claim_id = groups[claim_id]
            return claim_id

        for claim_id, claim in self.claims.items():
            ref = claim.get('restates')
            if ref is not None:
                if head(ref) == head(claim_id):
                    raise ValueError('折让重述关系出现循环')
                groups[head(claim_id)] = head(ref)
        # 组内所有金额必须一致，重述一笔折让不得产生第二笔钱。
        members = {}
        for claim_id in self.claims:
            members.setdefault(head(claim_id), []).append(claim_id)
        for root, ids in members.items():
            cents = [self.claims[cid]['cents'] for cid in ids
                    if self.claims[cid].get('cents') is not None]
            if cents and any(value != cents[0] for value in cents):
                raise ValueError('重述的折让金额必须与原折让一致')
        self.groups = {claim_id: head(claim_id) for claim_id in self.claims}

    def allowance_head(self, claim_id):
        return self.groups[claim_id]

    def allowance_members(self, claim_id):
        root = self.groups[claim_id]
        return sorted(cid for cid, value in self.groups.items() if value == root)

    def is_effective(self, claim):
        """义务方（供货方）提出或同意的主张才可据以扣款或放款。

        转述主张本身无效；当供货方另发一封"同意"主张指向它时，
        视为对转述内容的确认，转为有效。
        """
        if claim['party'] != '供货方':
            return False
        stance = claim.get('stance', '提出')
        if stance == '转述':
            return any(
                self.claims[agreement_id].get('stance', '提出') != '转述'
                and self.claims[agreement_id]['party'] == '供货方'
                for agreement_id in self.agreements.get(claim['id'], [])
            )
        return True

    def quote(self, claim_id):
        claim = self.claims[claim_id]
        message = self.messages[claim['mail']]
        start, end = claim['span']
        return message['text'][start:end]

    def resolve(self, claim):
        """给出主张适用的订单行候选与批次指代，解析不了时返回全部候选。"""
        explicit = claim.get('lines')
        if explicit:
            return list(explicit), '主张直接列明候选行'
        invoice = claim.get('invoice')
        batch = claim.get('batch')
        lines = [line for line in self.orders
                 if invoice is None or line['invoice'] == invoice]
        if batch is not None:
            matched = [line['line'] for line in lines if line['batch'] == batch]
            if matched:
                return matched, '按发票与批次指代解析'
        if invoice is not None:
            return [line['line'] for line in lines], '批次指代不明，返回同发票候选行'
        return [line['line'] for line in self.orders], '缺少发票与批次，返回全部候选行'

    def list_claims(self):
        rows = []
        for claim_id, claim in self.claims.items():
            candidates, reason = self.resolve(claim)
            rows.append({
                'claim': claim_id,
                'mail': claim['mail'],
                'party': claim['party'],
                'stance': claim.get('stance', '提出'),
                'kind': claim['kind'],
                'cents': claim.get('cents'),
                'invoice': claim.get('invoice'),
                'batch': claim.get('batch'),
                'condition': claim.get('condition'),
                'allowance': self.groups[claim_id],
                'restates': claim.get('restates'),
                'agrees_to': claim.get('agrees_to'),
                'forwarded': claim.get('stance', '提出') == '转述',
                'effective': self.is_effective(claim),
                'candidate_lines': candidates,
                'resolution': reason,
                'quote': self.quote(claim_id),
            })
        return rows
