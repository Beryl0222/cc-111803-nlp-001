"""对账复核引擎。

在主张台账之上处理本期折让分配，输出每个订单行的可付、暂留与补差：

* 候选解释之间对某行可付余额没有影响时，该行照常结算；只有受影响的部分暂留。
* 上一期已确认的分配保持锁定，新材料只能与之比较，不能悄悄替换；冲突指明
  涉及的金额并交还对账员。
* 已入账付款仍按原付款指令查阅，新结论与旧指令的差额走补差；同一笔折让的
  占用在重述链上合并计算，不会被重复花掉。
"""

from collections import defaultdict

from .claims import ClaimLedger


class ReviewDesk:
    def __init__(self, lines, receipts, claims, mail, payments=None,
                 prior_allocations=None):
        self.lines = {line['line']: line for line in lines}
        self.received = {receipt['line'] for receipt in receipts}
        self.ledger = mail if isinstance(mail, ClaimLedger) else ClaimLedger(mail, lines)
        self.ledger.load(claims)
        self.payments = {payment['line']: payment for payment in (payments or [])
                         if payment['posted']}
        self.prior = {self._locked_key(item): item for item in (prior_allocations or [])}

    @staticmethod
    def _key(allocation):
        return allocation['claim'], allocation['line']

    def _locked_key(self, allocation):
        # 重述主张与原主张是同一笔折让，锁定关系按折让组与订单行匹配。
        claim_id, line_id = self._key(allocation)
        return self.ledger.allowance_head(claim_id), line_id

    def review(self, allocations, candidate_sets=None, strict=True):
        candidate_sets = candidate_sets or []
        conflicts = self._collect_conflicts(allocations, candidate_sets)
        if strict and conflicts:
            conflict = conflicts[0]
            raise ValueError(
                '已确认的分配不能替换：'
                f"{conflict['claim']} 对 {conflict['line']} 锁定 "
                f"{conflict['locked_cents']} 分，新材料主张 "
                f"{conflict['proposed_cents']} 分，冲突部分交还对账员"
            )

        screen = self._screen(allocations)
        entitled_line, pending_line = screen['entitled_line'], screen['pending_line']
        contested = self._scenario_risk(candidate_sets)

        self._check_capacity(screen)

        rows = {}
        for line_id, line in self.lines.items():
            payment = self.payments.get(line_id)
            posted = payment['amount_cents'] if payment else None
            payable_discount = entitled_line[line_id]
            held_discount = pending_line[line_id] + contested[line_id]
            net_payable = line['amount_cents'] - payable_discount
            reasons = list(screen['gating'][line_id])
            if contested[line_id]:
                reasons.append('候选分配对本行可付余额有分歧，分歧部分暂留')
            rows[line_id] = {
                'line': line_id,
                'batch': line['batch'],
                'invoice': line['invoice'],
                'line_amount_cents': line['amount_cents'],
                'discount_entitled_cents': payable_discount,
                'discount_held_cents': held_discount,
                'scenario_contested_cents': contested[line_id],
                'payable_cents': max(net_payable, 0) if payment is None else 0,
                'posted_cents': posted,
                'adjustment_cents': (net_payable - posted) if posted is not None else 0,
                'allowance_ids': sorted({
                    self.ledger.allowance_head(claim_id)
                    for claim_id in screen['claim_refs'][line_id]
                    if self.ledger.claims[claim_id]['kind'] == '折让'
                }),
                'claims': screen['claim_refs'][line_id],
                'basis': self._basis(line_id, screen['sources']),
                'change_sources': sorted(screen['sources'][line_id]),
                'reasons': reasons,
            }
        return {
            'lines': [rows[line_id] for line_id in self.lines],
            'allowances': self._allowance_ledger(screen),
            'claims': self.ledger.list_claims(),
            'conflicts': conflicts,
        }

    # -- 分配筛选 -------------------------------------------------------

    def _screen(self, allocations):
        entitled_line = defaultdict(int)
        pending_line = defaultdict(int)
        entitled_detail = defaultdict(int)   # (claim_id, line) -> cents
        pending_detail = defaultdict(int)
        sources = defaultdict(set)
        refs = defaultdict(list)
        gating = defaultdict(list)
        for allocation in allocations:
            cents = allocation['cents']
            if type(cents) is not int or cents <= 0:
                raise ValueError('折让金额须为正整数分')
            claim = self.ledger.claims[allocation['claim']]
            line_id = allocation['line']
            if line_id not in self.lines:
                raise ValueError('分配指向不存在的订单行')
            refs[line_id].append(claim['id'])
            for source in allocation.get('sources', []):
                sources[line_id].add(source)
            key = (claim['id'], line_id)
            reason = self._gating_reason(claim, allocation)
            if reason is None:
                entitled_line[line_id] += cents
                entitled_detail[key] += cents
            else:
                pending_line[line_id] += cents
                pending_detail[key] += cents
                gating[line_id].append(reason)
        return {
            'entitled_line': entitled_line,
            'pending_line': pending_line,
            'entitled_detail': entitled_detail,
            'pending_detail': pending_detail,
            'sources': sources,
            'claim_refs': refs,
            'gating': gating,
        }

    def _gating_reason(self, claim, allocation):
        if claim['kind'] != '折让':
            return '非折让主张不进入折让分配'
        if not self.ledger.is_effective(claim):
            if claim.get('stance') == '转述':
                return '转述内容未经供货方确认，暂不据以扣款'
            return '主张方无义务处分该款项，暂留'
        if claim.get('condition') and not allocation.get('condition_met'):
            return f"适用条件未满足：{claim['condition']}"
        if not allocation.get('approved'):
            return '分配尚未经确认，暂留'
        if allocation['line'] not in self.received:
            return '签收清单未补齐，暂留'
        return None

    def _check_capacity(self, screen):
        line_totals = defaultdict(int)
        for (claim_id, line_id), cents in screen['entitled_detail'].items():
            line_totals[line_id] += cents
        for line_id, total in line_totals.items():
            if total > self.lines[line_id]['amount_cents']:
                raise ValueError('分配超过订单行余额')
        # 已确认与暂留的分配同属一笔折让，合并计算占用，防止重复花用。
        group_totals = defaultdict(int)
        for bucket in ('entitled_detail', 'pending_detail'):
            for (claim_id, line_id), cents in screen[bucket].items():
                group_totals[self.ledger.allowance_head(claim_id)] += cents
        for root, total in group_totals.items():
            cap = self.ledger.claims[root].get('cents')
            if cap is not None and total > cap:
                raise ValueError('分配超过邮件折让金额')

    # -- 候选场景 -------------------------------------------------------

    def _scenario_risk(self, candidate_sets):
        """同一张折让在多种解释下分别试算，比较各行的可付余额。"""
        contested = defaultdict(int)
        if not candidate_sets:
            return contested
        scenario_entitled = []
        for candidates in candidate_sets:
            totals = defaultdict(int)
            for allocation in candidates:
                claim = self.ledger.claims[allocation['claim']]
                if self._gating_reason(claim, allocation) is None:
                    totals[allocation['line']] += allocation['cents']
            scenario_entitled.append(totals)
        baseline = scenario_entitled[0]
        for totals in scenario_entitled[1:]:
            for line_id in self.lines:
                difference = abs(baseline.get(line_id, 0) - totals.get(line_id, 0))
                contested[line_id] = max(contested[line_id], difference)
        return contested

    # -- 锁定与冲突 -----------------------------------------------------

    def _collect_conflicts(self, allocations, candidate_sets):
        conflicts = []
        for allocation in allocations:
            locked = self.prior.get(self._locked_key(allocation))
            if locked is not None and allocation['cents'] != locked['cents']:
                conflicts.append(self._conflict_row(locked, allocation))
        for index, candidates in enumerate(candidate_sets):
            for allocation in candidates:
                locked = self.prior.get(self._locked_key(allocation))
                if locked is not None and allocation['cents'] != locked['cents']:
                    row = self._conflict_row(locked, allocation)
                    row['scenario'] = index
                    conflicts.append(row)
        return conflicts

    def _conflict_row(self, locked, allocation):
        claim_id, line_id = self._key(allocation)
        return {
            'claim': claim_id,
            'line': line_id,
            'locked_claim': locked['claim'],
            'locked_cents': locked['cents'],
            'proposed_cents': allocation['cents'],
            'conflicted_cents': abs(allocation['cents'] - locked['cents']),
            'quote': self.ledger.quote(claim_id),
            'resolution': '冲突金额留款，交还对账员处理',
        }

    # -- 折让占用台账 ---------------------------------------------------

    def _allowance_ledger(self, screen):
        used = defaultdict(int)
        held = defaultdict(int)
        for (claim_id, line_id), cents in screen['entitled_detail'].items():
            used[self.ledger.allowance_head(claim_id)] += cents
        for (claim_id, line_id), cents in screen['pending_detail'].items():
            held[self.ledger.allowance_head(claim_id)] += cents
        rows = []
        for root, claim in self.ledger.claims.items():
            if self.ledger.allowance_head(root) != root or claim['kind'] != '折让':
                continue
            total = claim.get('cents')
            occupied = used[root]
            pending = held[root]
            rows.append({
                'allowance': root,
                'total_cents': total,
                'occupied_cents': occupied,
                'held_cents': pending,
                'remaining_cents': None if total is None else total - occupied - pending,
                'restated_by': [cid for cid in self.ledger.allowance_members(root)
                                if cid != root],
                'allocations': sorted(
                    [line_id for (claim_id, line_id) in screen['entitled_detail']
                     if self.ledger.allowance_head(claim_id) == root]
                    + [line_id for (claim_id, line_id) in screen['pending_detail']
                       if self.ledger.allowance_head(claim_id) == root]
                ),
            })
        return rows

    # -- 依据展示 -------------------------------------------------------

    def _basis(self, line_id, sources):
        return [{'source': source, 'effect': '改变本行本期结论的原始材料'}
                for source in sorted(sources[line_id])]
