from thesistally.baseline_funnel import stop_failures
from thesistally.signals import rules


def test_stop_components_disclose_overlaps_and_include_exact_limits():
    f = rules(revision="r1")["filters"]
    assert stop_failures(100, 95, 1, f) == ["above_max_atr"]
    assert stop_failures(100, 89, 4, f) == ["above_max_pct", "above_max_atr"]
    assert stop_failures(100, 97, 1.2, f) == []
    assert stop_failures(100, 90, 4, f) == []
    assert stop_failures(100, 99, 5, f) == ["below_min_pct"]
