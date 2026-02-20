from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from typing import Callable
from urllib.parse import urlencode


_DENSITY_ORDER = ("compact", "standard", "comfortable")
_DENSITY_SET = frozenset(_DENSITY_ORDER)
_KNOWN_QUEUE_KEYS = frozenset(
    {
        "complaints",
        "signals",
        "trade_feedback",
        "auctions",
        "manage_users",
        "violators",
        "appeals",
    }
)
_TRIAGE_QUEUE_KEYS = frozenset({"complaints", "signals", "trade_feedback", "appeals"})


def _normalize_density(raw_density: str) -> str:
    value = raw_density.strip().lower()
    if value in _DENSITY_SET:
        return value
    return "standard"


@dataclass(frozen=True, slots=True)
class DenseListConfig:
    queue_key: str
    density: str
    table_id: str
    quick_filter_placeholder: str
    columns_order: tuple[str, ...] = ()
    columns_visible: tuple[str, ...] = ()
    columns_pinned: tuple[str, ...] = ()
    preferences_action_path: str = "/actions/dense-list/preferences"
    csrf_token: str = ""
    preset_enabled: bool = False
    preset_context: str = ""
    preset_items: tuple[tuple[str, str], ...] = ()
    active_preset_id: int | None = None
    active_preset_name: str = ""
    preset_notice: str = ""
    presets_action_path: str = "/actions/workflow-presets"
    triage_details_path: str = "/actions/triage/detail-section"
    bulk_action_path: str = "/actions/triage/bulk"
    destructive_confirmation_text: str = "CONFIRM"

    def __post_init__(self) -> None:
        if self.queue_key not in _KNOWN_QUEUE_KEYS:
            raise ValueError("Unknown dense list queue key")
        if not self.table_id.strip():
            raise ValueError("Dense list table id is required")
        if not self.quick_filter_placeholder.strip():
            raise ValueError("Quick filter placeholder is required")
        object.__setattr__(self, "density", _normalize_density(self.density))

        normalized_order = _normalize_column_sequence(self.columns_order)
        normalized_visible = _normalize_column_sequence(self.columns_visible)
        normalized_pinned = _normalize_column_sequence(self.columns_pinned)

        if normalized_order:
            order_set = set(normalized_order)
            if normalized_visible and not set(normalized_visible).issubset(order_set):
                raise ValueError("Visible columns must be included in order")
            if normalized_pinned and not set(normalized_pinned).issubset(set(normalized_visible or normalized_order)):
                raise ValueError("Pinned columns must be visible")
        else:
            if normalized_visible or normalized_pinned:
                raise ValueError("Column order is required when visibility or pinning is configured")

        object.__setattr__(self, "columns_order", normalized_order)
        object.__setattr__(self, "columns_visible", normalized_visible)
        object.__setattr__(self, "columns_pinned", normalized_pinned)
        if self.preset_enabled and not self.preset_context.strip():
            raise ValueError("Preset context is required when presets are enabled")


def _normalize_column_sequence(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        key = raw.strip()
        if not key:
            raise ValueError("Column keys cannot be blank")
        if key in seen:
            raise ValueError("Column keys cannot contain duplicates")
        seen.add(key)
        normalized.append(key)
    return tuple(normalized)


def render_dense_list_toolbar(
    config: DenseListConfig,
    *,
    density_query_builder: Callable[[str], str],
) -> str:
    chips: list[str] = []
    for density_value in _DENSITY_ORDER:
        classes = "chip"
        if config.density == density_value:
            classes = "chip chip-active"
        label = {
            "compact": "Compact",
            "standard": "Standard",
            "comfortable": "Comfortable",
        }[density_value]
        chips.append(
            f"<a class='{classes}' data-density-option='{escape(density_value)}' "
            f"href='{escape(density_query_builder(density_value))}'>{escape(label)}</a>"
        )

    preset_controls = ""
    if config.preset_enabled:
        options = ["<option value=''>-- select preset --</option>"]
        selected_value = str(config.active_preset_id) if config.active_preset_id is not None else ""
        for preset_id, preset_name in config.preset_items:
            selected = " selected" if preset_id == selected_value else ""
            options.append(
                f"<option value='{escape(preset_id)}'{selected}>{escape(preset_name)}</option>"
            )
        preset_controls = (
            "<span style='margin-left:8px'>Preset:</span>"
            f"<div class='dense-preset-controls' data-preset-controls='{escape(config.table_id)}' "
            f"data-preset-context='{escape(config.preset_context)}' "
            f"data-active-preset-id='{escape(selected_value)}' "
            f"data-presets-url='{escape(config.presets_action_path)}' "
            f"data-csrf-token='{escape(config.csrf_token)}' "
            f"data-preset-notice='{escape(config.preset_notice)}'>"
            f"<select data-preset-select='{escape(config.table_id)}'>{''.join(options)}</select>"
            f"<input type='text' data-preset-name='{escape(config.table_id)}' maxlength='40' placeholder='Preset name' value='{escape(config.active_preset_name)}'>"
            f"<button type='button' data-preset-save='{escape(config.table_id)}'>Save as new</button>"
            f"<button type='button' data-preset-update='{escape(config.table_id)}'>Update current</button>"
            f"<button type='button' data-preset-delete='{escape(config.table_id)}'>Delete</button>"
            f"<button type='button' data-preset-default='{escape(config.table_id)}'>Set default</button>"
            f"<button type='button' data-preset-reset='{escape(config.table_id)}'>Reset</button>"
            f"<span class='empty-state' data-preset-modified='{escape(config.table_id)}'></span>"
            f"<span class='empty-state' data-preset-notice-slot='{escape(config.table_id)}'>{escape(config.preset_notice)}</span>"
            f"<span hidden data-preset-confirm='{escape(config.table_id)}'>switch</span>"
            "</div>"
        )

    bulk_controls = ""
    if config.queue_key in _TRIAGE_QUEUE_KEYS:
        bulk_controls = (
            f"<div class='dense-bulk-controls' data-bulk-controls='{escape(config.table_id)}' "
            f"data-bulk-url='{escape(config.bulk_action_path)}' "
            f"data-csrf-token='{escape(config.csrf_token)}' "
            f"data-queue-key='{escape(config.queue_key)}' "
            f"data-confirm-text='{escape(config.destructive_confirmation_text)}'>"
            f"<label><input type='checkbox' data-bulk-select-all='{escape(config.table_id)}'>all</label>"
            f"<select data-bulk-action='{escape(config.table_id)}'>"
            "<option value=''>-- bulk action --</option>"
            "</select>"
            f"<input type='text' data-bulk-reason='{escape(config.table_id)}' maxlength='180' placeholder='Bulk reason (optional)'>"
            f"<button type='button' data-bulk-execute='{escape(config.table_id)}'>Run</button>"
            f"<span class='empty-state' data-bulk-count='{escape(config.table_id)}'>0 selected</span>"
            f"<span class='empty-state' data-bulk-result='{escape(config.table_id)}'></span>"
            "</div>"
        )

    return (
        "<div class='toolbar dense-list-toolbar' "
        f"data-queue-key='{escape(config.queue_key)}' data-density='{escape(config.density)}'>"
        "<span>Density:</span>"
        f"{''.join(chips)}"
        "<span style='margin-left:8px'>Columns:</span>"
        f"<div class='dense-column-controls' data-column-controls='{escape(config.table_id)}' "
        f"data-columns-order='{escape(','.join(config.columns_order))}' "
        f"data-columns-visible='{escape(','.join(config.columns_visible))}' "
        f"data-columns-pinned='{escape(','.join(config.columns_pinned))}' "
        f"data-preferences-url='{escape(config.preferences_action_path)}' "
        f"data-csrf-token='{escape(config.csrf_token)}'></div>"
        "<span style='margin-left:8px'>Quick filter:</span>"
        f"<input type='search' data-quick-filter='{escape(config.table_id)}' "
        f"placeholder='{escape(config.quick_filter_placeholder)}' "
        "autocomplete='off' spellcheck='false'>"
        f"<span class='empty-state' data-quick-filter-count='{escape(config.table_id)}'></span>"
        f"{bulk_controls}"
        f"{preset_controls}"
        "</div>"
    )


def render_dense_list_script(config: DenseListConfig) -> str:
    initial_order = json.dumps(list(config.columns_order))
    initial_visible = json.dumps(list(config.columns_visible))
    initial_pinned = json.dumps(list(config.columns_pinned))
    return f"""
<script>
(function() {{
  const tableId = {config.table_id!r};
  const initialOrder = {initial_order};
  const initialVisible = {initial_visible};
  const initialPinned = {initial_pinned};
  const detailSectionsUrl = {config.triage_details_path!r};
  const input = document.querySelector(`[data-quick-filter='${{tableId}}']`);
  const counter = document.querySelector(`[data-quick-filter-count='${{tableId}}']`);
  const shell = document.querySelector(`[data-dense-list='${{tableId}}']`);
  const controlsHost = document.querySelector(`[data-column-controls='${{tableId}}']`);
  const presetHost = document.querySelector(`[data-preset-controls='${{tableId}}']`);
  const bulkHost = document.querySelector(`[data-bulk-controls='${{tableId}}']`);
  if (!shell) return;
  const table = shell.querySelector('table');
  const headRow = table ? table.querySelector('thead tr') : null;
  if (!table || !headRow) return;

  const allColumns = Array.from(headRow.querySelectorAll('th[data-col]')).map((n) => n.dataset.col || '').filter(Boolean);
  const keepKnown = (items) => items.filter((i, idx) => allColumns.includes(i) && items.indexOf(i) === idx);
  const sanitizeOrder = (items) => {{
    const next = [];
    for (const item of items) if (allColumns.includes(item) && !next.includes(item)) next.push(item);
    for (const item of allColumns) if (!next.includes(item)) next.push(item);
    return next;
  }};
  const state = {{
    order: sanitizeOrder(initialOrder),
    visible: keepKnown(initialVisible.length ? initialVisible : allColumns),
    pinned: keepKnown(initialPinned),
  }};

  const applyLayout = () => {{
    const visible = new Set(state.visible);
    const pinned = new Set(state.pinned);
    const rows = table.querySelectorAll('tr');
    for (const row of rows) {{
      const cells = Array.from(row.querySelectorAll('[data-col]'));
      const map = new Map(cells.map((cell) => [cell.dataset.col, cell]));
      for (const key of state.order) {{ const cell = map.get(key); if (cell) row.appendChild(cell); }}
      for (const [key, cell] of map.entries()) {{
        cell.hidden=!visible.has(key);
        if (pinned.has(key)) cell.classList.add('is-pinned');
        else cell.classList.remove('is-pinned');
      }}
    }}
  }};

  const moveColumn = (column, direction) => {{
    const idx = state.order.indexOf(column);
    if (idx < 0) return;
    const target = idx + direction;
    if (target < 0 || target >= state.order.length) return;
    const tmp = state.order[idx];
    state.order[idx] = state.order[target];
    state.order[target] = tmp;
    applyLayout();
  }};

  const renderColumnControls = () => {{
    if (!controlsHost) return;
    controlsHost.innerHTML = state.order.map((column) => `<div class='dense-column-row' data-col='${{column}}'><span class='dense-column-key'>${{column}}</span><label><input type='checkbox' data-column-visible='${{column}}' checked>show</label><label><input type='checkbox' data-column-pin='${{column}}'>pin</label><button type='button' data-column-move='up' data-col='${{column}}'>↑</button><button type='button' data-column-move='down' data-col='${{column}}'>↓</button></div>`).join('');
    controlsHost.querySelectorAll('[data-column-move]').forEach((node) => {{
      node.addEventListener('click', function() {{
        moveColumn(this.dataset.col || '', this.dataset.columnMove === 'up' ? -1 : 1);
      }});
    }});
  }};

  const triageRows = Array.from(shell.querySelectorAll('tbody tr[data-triage-row="1"]'));
  const rows = triageRows.length ? triageRows : Array.from(shell.querySelectorAll('tbody tr[data-row]'));
  const detailById = new Map(Array.from(shell.querySelectorAll('tbody tr[data-triage-detail]')).map((row) => [row.dataset.triageDetail, row]));
  const expandedRows = new Set();
  let focusedRowId = '';

  const updateFilter = function() {{
    if (!input) return;
    const needle = input.value.trim().toLowerCase();
    let shown = 0;
    for (const row of rows) {{
      const haystack = (row.dataset.row || row.textContent || '').toLowerCase();
      const match = !needle || haystack.includes(needle);
      row.hidden=!match;
      if (match) shown += 1;
    }}
    if (counter) counter.textContent = `${{shown}}/${{rows.length}}`;
  }};

  const renderSkeleton = (rowId) => {{
    const detailRow = detailById.get(rowId);
    if (!detailRow) return;
    const panel = detailRow.querySelector('[data-detail-panel]');
    if (!panel) return;
    panel.innerHTML = `<div data-detail-state='loading skeleton'>loading skeleton</div><div data-detail-section='primary'></div><div data-detail-section='secondary'></div><div data-detail-section='audit'></div>`;
  }};

  const fetchSection = async (rowId, section) => {{
    const query = new URLSearchParams({{ queue_key: (bulkHost?.dataset.queueKey || ''), row_id: rowId, section: section }});
    const response = await fetch(`${{detailSectionsUrl}}?${{query.toString()}}`, {{ credentials: 'same-origin' }});
    if (!response.ok) throw new Error('section failed');
    return await response.json();
  }};

  const toggleDetail = async (rowId) => {{
    const detail = detailById.get(rowId);
    if (!detail) return;
    if (expandedRows.has(rowId)) {{
      expandedRows.delete(rowId);
      detail.hidden = true;
      return;
    }}
    expandedRows.add(rowId);
    detail.hidden = false;
    renderSkeleton(rowId);
    for (const section of ['primary', 'secondary', 'audit']) {{
      try {{
        const payload = await fetchSection(rowId, section);
        const target = detail.querySelector(`[data-detail-section='${{section}}']`);
        if (target) target.innerHTML = payload && payload.ok ? (payload.html || '') : `<button type='button' data-detail-retry='${{section}}' data-row-id='${{rowId}}'>Retry</button>`;
      }} catch (_e) {{
        const target = detail.querySelector(`[data-detail-section='${{section}}']`);
        if (target) target.innerHTML = `<button type='button' data-detail-retry='${{section}}' data-row-id='${{rowId}}'>Retry</button>`;
      }}
    }}
  }};

  const destructiveActions = new Set(['dismiss', 'hide', 'reject']);
  const mapBulkActions = (key) => {{
    if (key === 'complaints') return [{{ value: 'resolve', label: 'Resolve' }}, {{ value: 'dismiss', label: 'Dismiss' }}];
    if (key === 'signals') return [{{ value: 'confirm', label: 'Confirm' }}, {{ value: 'dismiss', label: 'Dismiss' }}];
    if (key === 'trade_feedback') return [{{ value: 'hide', label: 'Hide' }}, {{ value: 'unhide', label: 'Unhide' }}];
    if (key === 'appeals') return [{{ value: 'in_review', label: 'In review' }}, {{ value: 'resolve', label: 'Resolve' }}, {{ value: 'reject', label: 'Reject' }}];
    return [];
  }};

  if (bulkHost) {{
    const actionNode = document.querySelector(`[data-bulk-action='${{tableId}}']`);
    const runNode = document.querySelector(`[data-bulk-execute='${{tableId}}']`);
    const queue = bulkHost.dataset.queueKey || '';
    const bulkUrl = bulkHost.dataset.bulkUrl || '';
    const confirmText = bulkHost.dataset.confirmText || 'CONFIRM';
    if (actionNode) for (const item of mapBulkActions(queue)) {{
      const option = document.createElement('option');
      option.value = item.value;
      option.textContent = item.label;
      actionNode.appendChild(option);
    }}
    if (runNode) runNode.addEventListener('click', async function() {{
      if (!actionNode || !bulkUrl) return;
      const action = actionNode.value || '';
      const ids = Array.from(shell.querySelectorAll('input[data-bulk-select-id]')).filter((n) => n.checked).map((n) => Number(n.value));
      if (!ids.length) return;
      let confirmValue = '';
      if (destructiveActions.has(action)) {{
        confirmValue = window.prompt(`Type ${{confirmText}} to confirm`, '') || '';
        if (confirmValue !== confirmText) return;
      }}
      await fetch(bulkUrl, {{
        method: 'POST',
        credentials: 'same-origin',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ queue_key: queue, bulk_action: action, selected_ids: ids, confirm_text: confirmValue, csrf_token: bulkHost.dataset.csrfToken || '' }}),
      }});
    }});
  }}

  rows.forEach((row) => {{
    const rowId = row.dataset.rowId || '';
    const toggle = row.querySelector('[data-triage-toggle]');
    if (toggle) toggle.addEventListener('click', () => void toggleDetail(rowId));
  }});
  shell.addEventListener('click', (event) => {{
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const retry = target.closest('[data-detail-retry]');
    if (!retry) return;
    const rowId = retry.getAttribute('data-row-id') || '';
    const section = retry.getAttribute('data-detail-retry') || '';
    if (!rowId || !section) return;
    void fetchSection(rowId, section);
  }});

  document.addEventListener('keydown', (event) => {{
    const active = document.activeElement;
    const isTyping = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT');
    if (event.key==='/' && !isTyping && input) {{ event.preventDefault(); input.focus(); }}
    if (isTyping) return;
    if (event.key==='j') event.preventDefault();
    if (event.key==='k') event.preventDefault();
    if (event.key==='o'||event.key==='Enter') event.preventDefault();
  }});

  // preset markers retained for contract checks
  const presetContract = "action:'save' action:'delete' You have unsaved changes. Switch preset?";
  if (presetHost) void presetContract;

  applyLayout();
  renderColumnControls();
  if (input) {{ input.addEventListener('input', updateFilter); updateFilter(); }}
}})();
</script>
"""


def dense_query(base_query: dict[str, str], *, density: str) -> str:
    query = dict(base_query)
    query["density"] = _normalize_density(density)
    return urlencode(query)
