"""Offline tests of the result-only collection gate, never product coverage."""

from itertools import product
from unittest.mock import Mock

import pytest

from tests.conftest import pytest_collection_modifyitems

pytestmark = pytest.mark.unit


def _item(*markers: str) -> Mock:
    item = Mock()
    item.get_closest_marker.side_effect = lambda name: getattr(pytest.mark, name) if name in markers else None
    return item


@pytest.mark.parametrize("internal,technical,deferred", list(product((False, True), repeat=3)))
def test_scope_options_are_independent_and_preserve_legacy_deferred(
    internal: bool, technical: bool, deferred: bool,
) -> None:
    """Each opt-in controls only its suite; legacy skips remain unchanged and ordered."""
    flags = {
        "--include-factor4-internal-calculation": internal,
        "--include-factor4-technical": technical,
        "--include-factor4-deferred": deferred,
    }
    config = Mock()
    config.getoption.side_effect = flags.__getitem__
    normal, internal_item, technical_item, legacy = (
        _item(), _item("factor4_internal_calculation"), _item("factor4_technical"), _item("factor4_deferred"),
    )
    combined = _item("factor4_internal_calculation", "factor4_technical")
    original = [normal, internal_item, technical_item, legacy, combined]
    selected = original.copy()

    pytest_collection_modifyitems(config, selected)

    expected = [normal]
    if internal:
        expected.append(internal_item)
    if technical:
        expected.append(technical_item)
    expected.append(legacy)
    if internal and technical:
        expected.append(combined)
    assert selected == expected
    excluded = [item for item in original if item not in expected]
    if excluded:
        config.hook.pytest_deselected.assert_called_once_with(items=excluded)
    else:
        config.hook.pytest_deselected.assert_not_called()
    for item in (normal, internal_item, technical_item, combined):
        item.add_marker.assert_not_called()
    if deferred:
        legacy.add_marker.assert_not_called()
    else:
        assert legacy.add_marker.call_args.args[0].name == "skip"


def test_unmarked_and_non_factor4_items_are_not_changed() -> None:
    """The gate cannot exclude unrelated suites or mark their fixtures skipped."""
    config = Mock()
    config.getoption.return_value = False
    ordinary, unrelated = _item(), _item("external_agent", "worker_contract")
    items = [ordinary, unrelated]
    pytest_collection_modifyitems(config, items)
    assert items == [ordinary, unrelated]
    config.hook.pytest_deselected.assert_not_called()
    ordinary.add_marker.assert_not_called()
    unrelated.add_marker.assert_not_called()
