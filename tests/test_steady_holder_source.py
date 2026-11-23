from thesistally import steady_actions


def test_combined_bonus_and_dividend_cash_on_original_units_only():
    subject = " Bonus 1:2/ Dividend - Rs 1.10 Per Share"
    term = {"bonus": {"dividend_per_original": 1.10, "subjects": [subject]}}
    q = {"other_actions": [subject], "dividend": 2.0}
    r = steady_actions.with_holder_dividend(q, term)
    assert r["dividend"] == 3.1
    assert r["other_actions"] == [subject]
    assert steady_actions.with_holder_dividend(r, term) == r
    assert steady_actions.with_holder_dividend({"dividend": 1.1}, term)["dividend"] == 1.1


def test_bse_receipt_excludes_changing_form_tokens_but_preserves_notice():
    marker = b'<table width="979"><td>20170721-21, issued 507940875</td></table>'
    tail = b'<td class="footerbox">website footer</td>'
    item = {"representation": "bse-notice-html-v1"}
    expected = marker + b"\n"
    assert steady_actions.evidence_bytes(item, b"server token A" + marker + tail) == expected
    assert steady_actions.evidence_bytes(item, b"server token B" + marker + tail) == expected


def test_shared_receipts_for_forward_and_historical_roots(tmp_path, monkeypatch):
    paths = []

    def fake(data_root, raw):
        paths.append(data_root)
        return {"actions": []}

    monkeypatch.setattr(steady_actions, "verified", fake)
    monkeypatch.setattr(steady_actions.corporate, "actions", lambda root: {})
    root = tmp_path / "data"
    assert steady_actions.terms(root) == steady_actions.terms(root / "stage3" / "market")
    assert paths == [root, root]
