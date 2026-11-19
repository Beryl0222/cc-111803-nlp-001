"""复核引擎业务规则测试：立场区分、重述防重扣、场景暂留、锁定冲突、补差。"""

import copy
import json
from pathlib import Path

import pytest

from nlp_invoice_dispute_brief.case_cli import reconcile, review
from nlp_invoice_dispute_brief.claims import ClaimLedger
from nlp_invoice_dispute_brief.review import ReviewDesk


FIXTURES = Path(__file__).parents[1] / 'fixtures'


@pytest.fixture
def sample():
    case = json.loads((FIXTURES / 'discounts.json').read_text())
    records = json.loads((FIXTURES / 'business.json').read_text())
    return case, records


def by_id(report, key):
    return {row['line']: row for row in report['lines']}[key]


# -- 立场区分：提出 / 同意 / 转述 --------------------------------------

def test_relayed_proposal_is_listed_but_not_effective(sample):
    case, records = sample
    report = review(case, records)
    relay = next(item for item in report['claims'] if item['claim'] == 'relay-4')
    assert relay['stance'] == '转述' and relay['forwarded'] is True
    assert relay['effective'] is False
    freight = next(item for item in report['claims'] if item['claim'] == 'freight-2')
    assert freight['effective'] is True and freight['kind'] == '运费'
    assert freight['candidate_lines'] == ['PO-41/2']
    goods = next(item for item in report['claims'] if item['claim'] == 'goods-3')
    assert goods['batch'] == '第一批' and goods['candidate_lines'] == ['PO-41/1']


def test_forwarded_span_cannot_be_a_direct_claim(sample):
    case, _ = sample
    case['claims'][0]['span'] = [13, 18]
    with pytest.raises(ValueError, match='转发内容'):
        review(case, json.loads((FIXTURES / 'business.json').read_text()))


def test_supplier_agreement_makes_relay_effective(sample):
    case, records = sample
    case['mail'].append({'id': 'mail-106', 'author': '供货方',
                         'text': '经办人说的 300 元我们同意。', 'forwarded': []})
    case['claims'].append({'id': 'agree-6', 'mail': 'mail-106', 'party': '供货方',
                           'stance': '同意', 'span': [0, 14], 'invoice': 'F-882',
                           'kind': '折让', 'cents': 30000, 'agrees_to': 'relay-4'})
    case['allocations'].append({'claim': 'relay-4', 'line': 'PO-41/1',
                                'cents': 5000, 'approved': True, 'condition_met': True})
    report = review(case, records)
    relay = next(item for item in report['claims'] if item['claim'] == 'relay-4')
    assert relay['effective'] is True
    assert by_id(report, 'PO-41/1')['discount_entitled_cents'] == 11000


def test_relay_allocation_is_held_without_agreement(sample):
    case, records = sample
    case['candidate_sets'] = []
    case['allocations'].append({'claim': 'relay-4', 'line': 'PO-41/1',
                                'cents': 30000, 'approved': True, 'condition_met': True})
    report = review(case, records)
    row = by_id(report, 'PO-41/1')
    assert row['discount_entitled_cents'] == 6000
    assert row['discount_held_cents'] == 30000
    assert any('转述内容未经供货方确认' in reason for reason in row['reasons'])


# -- 同一笔折让：重述归并与防重扣 --------------------------------------

def test_restated_discount_shares_one_allowance(sample):
    case, records = sample
    report = review(case, records)
    allowance = next(item for item in report['allowances']
                     if item['allowance'] == 'discount-1')
    assert allowance['total_cents'] == 10000
    assert allowance['restated_by'] == ['discount-5']
    assert allowance['remaining_cents'] == 0


def test_restated_discount_cannot_be_spent_a_second_time(sample):
    case, records = sample
    # mail-105 把同一笔一百元再分一次：与原分配合计 20000，超过折让总额。
    case['allocations'].extend([
        {'claim': 'discount-5', 'line': 'PO-41/1', 'cents': 6000,
         'approved': True, 'condition_met': True},
        {'claim': 'discount-5', 'line': 'PO-41/2', 'cents': 4000,
         'approved': True, 'condition_met': True},
    ])
    with pytest.raises(ValueError, match='超过邮件折让金额'):
        review(case, records)


def test_restatement_with_different_amount_is_rejected(sample):
    case, _ = sample
    case['claims'][4]['cents'] = 20000
    with pytest.raises(ValueError, match='重述的折让金额必须与原折让一致'):
        review(case, json.loads((FIXTURES / 'business.json').read_text()))


# -- 候选解释：只暂留有分歧的部分，其余照付 -----------------------------

def test_scenario_difference_holds_only_contested_part(sample):
    case, records = sample
    report = review(case, records)
    first, second = by_id(report, 'PO-41/1'), by_id(report, 'PO-41/2')
    # 两场景 6000/4000 与 5000/5000：每行分歧恰为 1000。
    assert first['scenario_contested_cents'] == 1000
    assert second['scenario_contested_cents'] == 1000
    # 无分歧的部分继续结算：第二批 82000 - 4000 = 78000 可付。
    assert second['payable_cents'] == 78000


def test_line_unaffected_by_candidates_settles_normally():
    # 三行场景：候选只在前两行之间移动，第三行不受影响，应照常结算。
    mail = [{'id': 'm1', 'author': '供货方', 'text': '折让一百元。', 'forwarded': []}]
    claims = [{'id': 'd1', 'mail': 'm1', 'party': '供货方', 'span': [0, 6],
               'invoice': 'F-1', 'kind': '折让', 'cents': 10000}]
    orders = [{'line': 'L1', 'batch': '第一批', 'invoice': 'F-1', 'amount_cents': 50000},
              {'line': 'L2', 'batch': '第二批', 'invoice': 'F-1', 'amount_cents': 50000},
              {'line': 'L3', 'batch': '第三批', 'invoice': 'F-1', 'amount_cents': 30000}]
    receipts = [{'ref': 'G1', 'line': 'L1', 'date': '2026-03-01'},
                {'ref': 'G2', 'line': 'L2', 'date': '2026-03-01'},
                {'ref': 'G3', 'line': 'L3', 'date': '2026-03-01'}]
    candidates = [
        [{'claim': 'd1', 'line': 'L1', 'cents': 10000, 'approved': True, 'condition_met': True}],
        [{'claim': 'd1', 'line': 'L2', 'cents': 10000, 'approved': True, 'condition_met': True}],
    ]
    desk = ReviewDesk(orders, receipts, claims, ClaimLedger(mail, orders))
    report = desk.review([], candidates)
    rows = {row['line']: row for row in report['lines']}
    assert rows['L1']['scenario_contested_cents'] == 10000
    assert rows['L2']['scenario_contested_cents'] == 10000
    assert rows['L3']['scenario_contested_cents'] == 0
    assert rows['L3']['payable_cents'] == 30000
    assert rows['L3']['reasons'] == []


# -- 已确认分配锁定：冲突交还，不能悄悄替换 -----------------------------

def test_confirmed_allocation_cannot_be_silently_replaced(sample):
    case, records = sample
    prior = copy.deepcopy(case['allocations'])
    # 新材料 mail-105 主张 5000/5000，与锁定的 6000/4000 冲突。
    case['allocations'] = [
        {'claim': 'discount-5', 'line': 'PO-41/1', 'cents': 5000,
         'approved': True, 'condition_met': True},
        {'claim': 'discount-5', 'line': 'PO-41/2', 'cents': 5000,
         'approved': True, 'condition_met': True},
    ]
    with pytest.raises(ValueError, match='已确认的分配不能替换'):
        review(case, records, prior)


def test_conflict_report_names_conflicted_amount(sample):
    case, records = sample
    prior = copy.deepcopy(case['allocations'])
    # 新材料 mail-105 主张 5000/5000，与锁定的 6000/4000 冲突。
    case['allocations'] = [
        {'claim': 'discount-5', 'line': 'PO-41/1', 'cents': 5000,
         'approved': True, 'condition_met': True},
        {'claim': 'discount-5', 'line': 'PO-41/2', 'cents': 5000,
         'approved': True, 'condition_met': True},
    ]
    with pytest.raises(ValueError, match='锁定 6000 分'):
        review(case, records, prior)

    # 非严格模式返回结构化冲突，指明涉及哪部分金额与原文。
    ledger = ClaimLedger(case['mail'], records['orders'])
    desk = ReviewDesk(records['orders'], records['receipts'], case['claims'],
                      ledger, payments=records['payments'], prior_allocations=prior)
    report = desk.review(case['allocations'], strict=False)
    conflict = report['conflicts'][0]
    assert conflict['line'] == 'PO-41/1'
    assert conflict['locked_cents'] == 6000 and conflict['proposed_cents'] == 5000
    assert conflict['conflicted_cents'] == 1000
    assert conflict['locked_claim'] == 'discount-1'
    assert '改分给两批' in conflict['quote']

    # 同额重申不构成冲突，折让占用仍为同一笔。
    case['allocations'] = [
        {'claim': 'discount-5', 'line': 'PO-41/1', 'cents': 6000,
         'approved': True, 'condition_met': True},
        {'claim': 'discount-5', 'line': 'PO-41/2', 'cents': 4000,
         'approved': True, 'condition_met': True},
    ]
    case['candidate_sets'] = []
    report = review(case, records, prior)
    assert report['conflicts'] == []
    row = next(item for item in report['allowances']
               if item['allowance'] == 'discount-1')
    assert row['occupied_cents'] == 10000 and row['remaining_cents'] == 0


# -- 签收与条件：补齐后重新判断，矛盾才留款 -----------------------------

def test_missing_receipt_holds_then_releases(sample):
    case, records = sample
    records['receipts'] = [receipt for receipt in records['receipts']
                           if receipt['line'] != 'PO-41/2']
    held = review(case, records)
    row = by_id(held, 'PO-41/2')
    assert row['discount_entitled_cents'] == 0
    assert row['discount_held_cents'] == 4000
    assert any('签收清单未补齐' in reason for reason in row['reasons'])
    # 签收补齐后重新判断：同一分配恢复可付。
    records['receipts'].append({'ref': 'GR-9', 'line': 'PO-41/2', 'date': '2026-03-06'})
    released = review(case, records)
    assert by_id(released, 'PO-41/2')['discount_entitled_cents'] == 4000


def test_unmet_condition_holds_claim(sample):
    case, records = sample
    case['claims'][0]['condition'] = '需仓库确认外箱完好'
    case['allocations'][0]['condition_met'] = False
    report = review(case, records)
    row = by_id(report, 'PO-41/1')
    assert row['discount_entitled_cents'] == 0
    assert any('适用条件未满足' in reason for reason in row['reasons'])


# -- 付款指令与到账：补差，不重花折让 -----------------------------------

def test_posted_payment_keeps_instruction_and_uses_adjustment(sample):
    case, records = sample
    report = review(case, records)
    row = by_id(report, 'PO-41/1')
    assert row['posted_cents'] == 120000
    assert row['payable_cents'] == 0
    # 新结论应净付 114000，原指令已付 120000，差额 -6000 走补差。
    assert row['adjustment_cents'] == -6000
    assert row['allowance_ids'] == ['discount-1']


def test_legacy_reconcile_output_unchanged(sample):
    case, records = sample
    posted, opened = reconcile(case, records)
    assert posted['posted'] == 120000 and posted['adjustment'] == -6000
    assert opened['payable'] == 78000 and opened['posted'] is None
    assert opened['evidence'] == ['这单折让一百元。']
