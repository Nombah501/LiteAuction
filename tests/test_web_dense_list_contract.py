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


def test_dense_list_contract_renders_column_controls_markup() -> None:
    config = DenseListConfig(
        queue_key="complaints",
        density="standard",
        table_id="complaints-table",
        quick_filter_placeholder="id / reason",
        columns_order=("id", "auction", "status"),
        columns_visible=("id", "status"),
        columns_pinned=("id",),
        csrf_token="csrf-token-value",
    )

    toolbar_html = render_dense_list_toolbar(config, density_query_builder=_density_path)

    assert "data-column-controls='complaints-table'" in toolbar_html
    assert "data-columns-order='id,auction,status'" in toolbar_html
    assert "data-columns-visible='id,status'" in toolbar_html
    assert "data-columns-pinned='id'" in toolbar_html
    assert "data-csrf-token='csrf-token-value'" in toolbar_html


def test_dense_list_script_contract_contains_order_and_pin_logic() -> None:
    config = DenseListConfig(
        queue_key="signals",
        density="compact",
        table_id="signals-table",
        quick_filter_placeholder="id",
        columns_order=("id", "auction", "status"),
        columns_visible=("id", "auction", "status"),
        columns_pinned=("id",),
    )

    script_html = render_dense_list_script(config)

    assert "data-column-move" in script_html
    assert "cell.classList.add('is-pinned')" in script_html
    assert "sanitizeOrder(" in script_html
    assert "moveColumn(" in script_html


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


def test_dense_list_filter_script_contract_contains_row_counter() -> None:
    config = DenseListConfig(
        queue_key="appeals",
        density="comfortable",
        table_id="appeals-table",
        quick_filter_placeholder="id / ref",
    )

    script_html = render_dense_list_script(config)

    assert "[data-quick-filter-count='${tableId}']" in script_html
    assert "row.hidden=!match" in script_html


def test_dense_list_contract_renders_preset_controls_when_enabled() -> None:
    config = DenseListConfig(
        queue_key="complaints",
        density="standard",
        table_id="complaints-table",
        quick_filter_placeholder="id / reason",
        preset_enabled=True,
        preset_context="moderation",
        preset_items=(("10", "Incident"), ("11", "Routine")),
        active_preset_id=10,
        active_preset_name="Incident",
        preset_notice="stale filter skipped",
    )

    toolbar_html = render_dense_list_toolbar(config, density_query_builder=_density_path)
    script_html = render_dense_list_script(config)

    assert "data-preset-controls='complaints-table'" in toolbar_html
    assert "data-preset-select='complaints-table'" in toolbar_html
    assert "data-preset-modified='complaints-table'" in toolbar_html
    assert "data-preset-confirm='complaints-table'" in toolbar_html
    assert "action:'save'" in script_html
    assert "action:'delete'" in script_html
    assert "You have unsaved changes. Switch preset?" in script_html


def test_dense_list_contract_renders_bulk_controls_for_triage_queues() -> None:
    config = DenseListConfig(
        queue_key="appeals",
        density="compact",
        table_id="appeals-table",
        quick_filter_placeholder="id / ref",
        csrf_token="csrf-token",
    )

    toolbar_html = render_dense_list_toolbar(config, density_query_builder=_density_path)
    script_html = render_dense_list_script(config)

    assert "data-bulk-controls='appeals-table'" in toolbar_html
    assert "data-bulk-action='appeals-table'" in toolbar_html
    assert "data-bulk-execute='appeals-table'" in toolbar_html
    assert "data-triage-row=\"1\"" in script_html
    assert "data-detail-section='primary'" in script_html
    assert "destructiveActions" in script_html


def test_dense_list_contract_contains_keyboard_shortcuts_contract() -> None:
    config = DenseListConfig(
        queue_key="signals",
        density="standard",
        table_id="signals-table",
        quick_filter_placeholder="id / user",
    )

    script_html = render_dense_list_script(config)

    assert "event.key==='/'" in script_html
    assert "event.key==='j'" in script_html
    assert "event.key==='k'" in script_html
    assert "event.key==='o'||event.key==='Enter'" in script_html
    assert "moveFocusedRow(1)" in script_html
    assert "moveFocusedRow(-1)" in script_html
    assert "toggleFocusedRowDetail()" in script_html
    assert "setFocusedRow(" in script_html


def test_dense_list_contract_wires_retry_hydration_contract() -> None:
    config = DenseListConfig(
        queue_key="complaints",
        density="standard",
        table_id="complaints-table",
        quick_filter_placeholder="id / reason",
    )

    script_html = render_dense_list_script(config)

    assert "const hydrateSection = async" in script_html
    assert "void hydrateSection(rowId, section);" in script_html
    assert "renderSectionRetry" in script_html
    assert "Section unavailable." in script_html


def test_dense_list_contract_wires_bulk_results_rendering_contract() -> None:
    config = DenseListConfig(
        queue_key="appeals",
        density="compact",
        table_id="appeals-table",
        quick_filter_placeholder="id / ref",
    )

    script_html = render_dense_list_script(config)

    assert "payload && Array.isArray(payload.results)" in script_html
    assert "const statusCell = row.querySelector(\"[data-status-cell='1'], [data-col='status']\")" in script_html
    assert "Needs attention:" in script_html
    assert "Bulk done:" in script_html


def test_dense_list_contract_includes_adaptive_depth_override_controls() -> None:
    config = DenseListConfig(
        queue_key="complaints",
        density="standard",
        table_id="complaints-table",
        quick_filter_placeholder="id / reason",
    )

    script_html = render_dense_list_script(config)

    assert "data-adaptive-override='auto'" in script_html
    assert "data-adaptive-override='inline_summary'" in script_html
    assert "data-adaptive-override='inline_full'" in script_html
    assert "query.set('depth_override', override);" in script_html
    assert "Adaptive depth:" in script_html
