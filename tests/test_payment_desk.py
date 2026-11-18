import json
from pathlib import Path

import pytest

from nlp_invoice_dispute_brief.case_cli import reconcile


@pytest.fixture
def sample():
    folder = Path(__file__).parents[1] / 'fixtures'
    return [json.loads((folder / name).read_text()) for name in ('discounts.json', 'business.json')]


def test_posted_and_open_lines_remain_distinct(sample):
    posted, opened = reconcile(*sample)
    assert posted['posted'] == 120000 and posted['adjustment'] == -6000
    assert opened['payable'] == 78000 and opened['posted'] is None
    assert opened['evidence'] == ['这单折让一百元。']


def test_same_discount_cannot_be_allocated_twice(sample):
    case, _ = sample
    case['allocations'][1]['cents'] = 6000
    with pytest.raises(ValueError, match='超过邮件折让金额'):
        reconcile(*sample)


def test_forwarded_quote_is_not_supplier_claim(sample):
    case, _ = sample
    case['claims'][0]['span'] = [13, 18]
    with pytest.raises(ValueError, match='转发内容'):
        reconcile(*sample)
