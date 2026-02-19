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
        "</div>"
    )


def render_dense_list_script(config: DenseListConfig) -> str:
    initial_order = json.dumps(list(config.columns_order))
    initial_visible = json.dumps(list(config.columns_visible))
    initial_pinned = json.dumps(list(config.columns_pinned))

    return (
        "<script>"
        "(function(){"
        f"const tableId={config.table_id!r};"
        f"const initialOrder={initial_order};"
        f"const initialVisible={initial_visible};"
        f"const initialPinned={initial_pinned};"
        "const input=document.querySelector(`[data-quick-filter='${tableId}']`);"
        "const counter=document.querySelector(`[data-quick-filter-count='${tableId}']`);"
        "const shell=document.querySelector(`[data-dense-list='${tableId}']`);"
        "const controlsHost=document.querySelector(`[data-column-controls='${tableId}']`);"
        "if(!shell){return;}"
        "const table=shell.querySelector('table');"
        "const headRow=table?table.querySelector('thead tr'):null;"
        "if(!table||!headRow){return;}"
        "const parseList=function(raw){"
        "if(!raw){return []; }"
        "return raw.split(',').map((item)=>item.trim()).filter(Boolean);"
        "};"
        "const allColumns=Array.from(headRow.querySelectorAll('th[data-col]')).map((el)=>el.dataset.col||'').filter(Boolean);"
        "if(!allColumns.length){return;}"
        "const byKey=function(items){return new Set(items);};"
        "const keepKnown=function(items){const known=byKey(allColumns);return items.filter((item,idx)=>known.has(item)&&items.indexOf(item)===idx);};"
        "const sanitizeOrder=function(items){"
        "const known=byKey(allColumns);"
        "const next=[];"
        "for(const item of items){if(known.has(item)&&!next.includes(item)){next.push(item);}}"
        "for(const item of allColumns){if(!next.includes(item)){next.push(item);}}"
        "return next;"
        "};"
        "const persistedOrder=controlsHost?parseList(controlsHost.dataset.columnsOrder):[];"
        "const persistedVisible=controlsHost?parseList(controlsHost.dataset.columnsVisible):[];"
        "const persistedPinned=controlsHost?parseList(controlsHost.dataset.columnsPinned):[];"
        "const state={"
        "order:sanitizeOrder(persistedOrder.length?persistedOrder:initialOrder),"
        "visible:keepKnown((persistedVisible.length?persistedVisible:initialVisible).length?(persistedVisible.length?persistedVisible:initialVisible):allColumns),"
        "pinned:keepKnown(persistedPinned.length?persistedPinned:initialPinned),"
        "};"
        "state.visible=state.order.filter((key)=>state.visible.includes(key));"
        "state.pinned=state.order.filter((key)=>state.pinned.includes(key)&&state.visible.includes(key));"
        "const densityValue=(shell.dataset.density||'standard').trim().toLowerCase();"
        "const saveUrl=controlsHost?controlsHost.dataset.preferencesUrl:'';"
        "const csrfToken=controlsHost?controlsHost.dataset.csrfToken:'';"
        "const queueKey=(shell.closest('[data-queue-key]')||document.querySelector('[data-queue-key]'))?.dataset.queueKey||'';"
        "let saveTimer=null;"
        "const queueSave=function(){"
        "if(!saveUrl||!csrfToken||!queueKey){return;}"
        "if(saveTimer){clearTimeout(saveTimer);}"
        "saveTimer=setTimeout(function(){"
        "saveTimer=null;"
        "void fetch(saveUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({queue_key:queueKey,density:densityValue,columns:{visible:state.visible,order:state.order,pinned:state.pinned},csrf_token:csrfToken})});"
        "},180);"
        "};"
        "const moveColumn=function(column,direction){"
        "const idx=state.order.indexOf(column);"
        "if(idx<0){return;}"
        "const target=idx+direction;"
        "if(target<0||target>=state.order.length){return;}"
        "const tmp=state.order[idx];"
        "state.order[idx]=state.order[target];"
        "state.order[target]=tmp;"
        "state.visible=state.order.filter((key)=>state.visible.includes(key));"
        "state.pinned=state.order.filter((key)=>state.pinned.includes(key)&&state.visible.includes(key));"
        "applyLayout();"
        "renderColumnControls();"
        "queueSave();"
        "};"
        "const applyLayout=function(){"
        "const visibleSet=byKey(state.visible);"
        "const pinnedSet=byKey(state.pinned);"
        "const rows=table.querySelectorAll('tr');"
        "for(const row of rows){"
        "const cells=Array.from(row.querySelectorAll('[data-col]'));"
        "const map=new Map(cells.map((cell)=>[cell.dataset.col,cell]));"
        "for(const key of state.order){const cell=map.get(key);if(cell){row.appendChild(cell);}}"
        "for(const [key,cell] of map.entries()){"
        "const show=visibleSet.has(key);"
        "cell.hidden=!show;"
        "if(show){cell.removeAttribute('aria-hidden');}else{cell.setAttribute('aria-hidden','true');}"
        "cell.classList.remove('is-pinned');"
        "cell.style.removeProperty('--pin-left');"
        "cell.style.removeProperty('z-index');"
        "}"
        "}"
        "let leftOffset=0;"
        "for(const key of state.order){"
        "if(!visibleSet.has(key)||!pinnedSet.has(key)){continue;}"
        "const pinnedCells=table.querySelectorAll(`[data-col='${key}']`);"
        "let columnWidth=0;"
        "for(const cell of pinnedCells){"
        "cell.classList.add('is-pinned');"
        "cell.style.setProperty('--pin-left',`${leftOffset}px`);"
        "if(cell.tagName==='TH'){cell.style.setProperty('z-index','4');columnWidth=Math.max(columnWidth,cell.offsetWidth);}"
        "else{cell.style.setProperty('z-index','3');columnWidth=Math.max(columnWidth,cell.offsetWidth);}"
        "}"
        "leftOffset+=columnWidth;"
        "}"
        "};"
        "const renderColumnControls=function(){"
        "if(!controlsHost){return;}"
        "const pieces=[];"
        "for(const column of state.order){"
        "const visibleChecked=state.visible.includes(column)?' checked':'';"
        "const pinChecked=state.pinned.includes(column)?' checked':'';"
        "pieces.push(`<div class='dense-column-row' data-col='${column}'><span class='dense-column-key'>${column}</span><label><input type='checkbox' data-column-visible='${column}'${visibleChecked}>show</label><label><input type='checkbox' data-column-pin='${column}'${pinChecked}>pin</label><button type='button' data-column-move='up' data-col='${column}'>↑</button><button type='button' data-column-move='down' data-col='${column}'>↓</button></div>`);"
        "}"
        "controlsHost.innerHTML=pieces.join('');"
        "controlsHost.querySelectorAll('[data-column-visible]').forEach((node)=>{"
        "node.addEventListener('change',function(){"
        "const key=this.dataset.columnVisible||'';"
        "if(!key){return;}"
        "if(this.checked){if(!state.visible.includes(key)){state.visible.push(key);state.visible=state.order.filter((item)=>state.visible.includes(item));}}"
        "else{state.visible=state.visible.filter((item)=>item!==key);state.pinned=state.pinned.filter((item)=>item!==key);}"
        "applyLayout();"
        "renderColumnControls();"
        "queueSave();"
        "});"
        "});"
        "controlsHost.querySelectorAll('[data-column-pin]').forEach((node)=>{"
        "node.addEventListener('change',function(){"
        "const key=this.dataset.columnPin||'';"
        "if(!key||!state.visible.includes(key)){this.checked=false;return;}"
        "if(this.checked){if(!state.pinned.includes(key)){state.pinned.push(key);state.pinned=state.order.filter((item)=>state.pinned.includes(item));}}"
        "else{state.pinned=state.pinned.filter((item)=>item!==key);}"
        "applyLayout();"
        "renderColumnControls();"
        "queueSave();"
        "});"
        "});"
        "controlsHost.querySelectorAll('[data-column-move]').forEach((node)=>{"
        "node.addEventListener('click',function(){"
        "const key=this.dataset.col||'';"
        "if(!key){return;}"
        "moveColumn(key,this.dataset.columnMove==='up'?-1:1);"
        "});"
        "});"
        "};"
        "const rows=Array.from(shell.querySelectorAll('tbody tr[data-row]'));"
        "const updateFilter=function(){"
        "if(!input){return;}"
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
        "applyLayout();"
        "renderColumnControls();"
        "if(input){input.addEventListener('input',updateFilter);updateFilter();}"
        "window.addEventListener('resize',applyLayout);"
        "})();"
        "</script>"
    )


def dense_query(base_query: dict[str, str], *, density: str) -> str:
    query = dict(base_query)
    query["density"] = _normalize_density(density)
    return urlencode(query)
