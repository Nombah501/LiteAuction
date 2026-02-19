from __future__ import annotations

import pytest

from app.web.dense_list import DenseListConfig, render_dense_list_script, render_dense_list_toolbar


def _density_path(density: str) -> str:
    return f"/queue?density={density}"


@pytest.mark.parametrize(
    "queue_key",
    [
        "complaints",
        "signals",
        "trade_feedback",
        "auctions",
        "manage_users",
        "violators",
        "appeals",
    ],
)
def test_dense_list_contract_accepts_all_queue_keys_contract(queue_key: str) -> None:
    config = DenseListConfig(
        queue_key=queue_key,
        density="standard",
        table_id="queue-table",
        quick_filter_placeholder="filter",
    )

    toolbar_html = render_dense_list_toolbar(config, density_query_builder=_density_path)
    script_html = render_dense_list_script(config)

    assert "data-density-option='compact'" in toolbar_html
    assert "data-density-option='standard'" in toolbar_html
    assert "data-density-option='comfortable'" in toolbar_html
    assert "data-quick-filter='queue-table'" in toolbar_html
    assert "[data-quick-filter='${tableId}']" in script_html
    assert "tbody tr[data-row]" in script_html


def test_dense_list_contract_marks_active_density_chip() -> None:
    config = DenseListConfig(
        queue_key="complaints",
        density="compact",
        table_id="complaints-table",
        quick_filter_placeholder="id / reason",
    )

    toolbar_html = render_dense_list_toolbar(config, density_query_builder=_density_path)

    assert "class='chip chip-active' data-density-option='compact'" in toolbar_html
    assert "class='chip' data-density-option='standard'" in toolbar_html


def test_dense_list_contract_rejects_unknown_queue_key() -> None:
    with pytest.raises(ValueError):
        DenseListConfig(
            queue_key="unknown",
            density="standard",
            table_id="queue-table",
            quick_filter_placeholder="filter",
        )


def test_dense_list_contract_normalizes_invalid_density() -> None:
    config = DenseListConfig(
        queue_key="complaints",
        density="WRONG",
        table_id="complaints-table",
        quick_filter_placeholder="id",
    )

    assert config.density == "standard"
