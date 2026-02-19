from __future__ import annotations

from dataclasses import dataclass
from html import escape
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

    def __post_init__(self) -> None:
        if self.queue_key not in _KNOWN_QUEUE_KEYS:
            raise ValueError("Unknown dense list queue key")
        if not self.table_id.strip():
            raise ValueError("Dense list table id is required")
        object.__setattr__(self, "density", _normalize_density(self.density))


def render_dense_list_toolbar(
    config: DenseListConfig,
    *,
    density_query_builder: callable,
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

    return (
        "<div class='toolbar dense-list-toolbar' "
        f"data-queue-key='{escape(config.queue_key)}' data-density='{escape(config.density)}'>"
        "<span>Density:</span>"
        f"{''.join(chips)}"
        "<span style='margin-left:8px'>Quick filter:</span>"
        f"<input type='search' data-quick-filter='{escape(config.table_id)}' "
        f"placeholder='{escape(config.quick_filter_placeholder)}' "
        "autocomplete='off' spellcheck='false'>"
        f"<span class='empty-state' data-quick-filter-count='{escape(config.table_id)}'></span>"
        "</div>"
    )


def render_dense_list_script(config: DenseListConfig) -> str:
    return (
        "<script>"
        "(function(){"
        f"const tableId={config.table_id!r};"
        "const input=document.querySelector(`[data-quick-filter='${tableId}']`);"
        "const counter=document.querySelector(`[data-quick-filter-count='${tableId}']`);"
        "const shell=document.querySelector(`[data-dense-list='${tableId}']`);"
        "if(!input||!shell){return;}"
        "const rows=Array.from(shell.querySelectorAll('tbody tr[data-row]'));"
        "const update=function(){"
        "const needle=input.value.trim().toLowerCase();"
        "let shown=0;"
        "for(const row of rows){"
        "const haystack=(row.dataset.row||row.textContent||'').toLowerCase();"
        "const match=!needle||haystack.includes(needle);"
        "row.hidden=!match;"
        "if(match){shown+=1;}"
        "}"
        "if(counter){counter.textContent=`${shown}/${rows.length}`;}"
        "};"
        "input.addEventListener('input',update);"
        "update();"
        "})();"
        "</script>"
    )


def dense_query(base_query: dict[str, str], *, density: str) -> str:
    query = dict(base_query)
    query["density"] = _normalize_density(density)
    return urlencode(query)
