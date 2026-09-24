#!/usr/bin/env python3
"""
appian_site_react_generator.py
================================
Converts an Appian "Site" design object, plus every Interface it links to,
into React (JSX) components -- recursively, the same way the node-converter
suite handles process nodes.

DEPENDS ON appian_common.py (same directory) -- reuses DesignObjectIndex and
DrillDownResolver so indexing/drill-down behavior stays identical across the
whole suite (Java node converters + this).

IMPORTANT ARCHITECTURAL CLARIFICATION -- READ BEFORE USING "ENDPOINTS" OUTPUT
---------------------------------------------------------------------------------
Appian interfaces do not call raw HTTP URLs directly. A button or field in an
interface triggers one of: a record query (a!queryRecordType), a process
start (a!startProcess), a related/record action, or a rule! call into an
Integration design object (which itself is normally invoked from a process
model, not straight from SAIL). So "endpoints.json" in the output is not a
list of URLs scraped out of the UI -- it's a list of BACKEND TOUCHPOINTS this
page depends on, each of which needs a corresponding REST endpoint exposed by
your generated Java backend (the same backend the node-converter scripts are
building). Any literal http(s):// strings that do appear (e.g. an external
link) are captured too, separately labeled.

TWO HONEST LIMITATIONS
-------------------------
1. XML parsing is permissive/regex-based (same caveat as the rest of the
   suite) -- run --inspect on a real exported Site and Interface file first
   and adjust DesignObjectIndex in appian_common.py if needed.
2. Rule-input binding is NOT done. When a business-logic rule is inlined
   (e.g. `rule!isEligible(balance: pv!currentBalance)`), its body is spliced
   in as-is with its own `ri!balance` references -- they are NOT rewritten to
   `pv!currentBalance`. This is the same simplification the Script Task
   converter uses. It's fine for seeing WHAT logic runs and WHAT it depends
   on, but a human (or a follow-on pass that also parses each rule's
   ruleInputs signature) needs to do the final parameter binding before the
   generated JS is correct.

USAGE
-----
Demo (self-contained, no real data needed):
    python appian_site_react_generator.py --demo

Inspect one exported file to calibrate the parser:
    python appian_site_react_generator.py --inspect /path/to/some_interface.xml

Real run:
    python appian_site_react_generator.py \\
        --export-root /path/to/local/appian_export \\
        --site-report /path/to/site_report.json \\
        --out-dir ./react_output
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from appian_common import DesignObjectIndex, DesignObject, DrillDownResolver, NodeRecord, best_effort_translate


# ---------------------------------------------------------------------------
# Site / page model
# ---------------------------------------------------------------------------

@dataclass
class SitePage:
    name: str
    url_stub: str
    interface_ref: str


@dataclass
class SiteModel:
    name: str
    pages: list = field(default_factory=list)


PAGE_BLOCK_PATTERN = re.compile(r"<page[^>]*>(.*?)</page>", re.IGNORECASE | re.DOTALL)
NAME_PATTERN = re.compile(r"<name[^>]*>([^<]+)</name>", re.IGNORECASE)
URLSTUB_PATTERN = re.compile(r"<urlStub[^>]*>([^<]+)</urlStub>", re.IGNORECASE)
INTERFACE_REF_PATTERN = re.compile(r"<interface[^>]*>([^<]+)</interface>", re.IGNORECASE)


def load_site_from_report(site_report_path: Path) -> SiteModel:
    """
    ADAPT to your actual site report schema. Assumes:
    {"name": "...", "pages": [{"name":..., "urlStub":..., "interfaceRef":...}, ...]}
    """
    data = json.loads(site_report_path.read_text(encoding="utf-8"))
    pages = [SitePage(name=p["name"], url_stub=p.get("urlStub", p["name"]), interface_ref=p["interfaceRef"])
             for p in data.get("pages", [])]
    return SiteModel(name=data.get("name", "Site"), pages=pages)


def parse_site_xml(text: str) -> SiteModel:
    """Fallback permissive parser if you point --export-root at a raw exported Site XML instead
    of a pre-built JSON report. Calibrate against --inspect output."""
    name_m = NAME_PATTERN.search(text)
    site_name = name_m.group(1).strip() if name_m else "Site"
    pages = []
    for block in PAGE_BLOCK_PATTERN.findall(text):
        n = NAME_PATTERN.search(block)
        u = URLSTUB_PATTERN.search(block)
        i = INTERFACE_REF_PATTERN.search(block)
        if n and i:
            pages.append(SitePage(name=n.group(1).strip(),
                                   url_stub=(u.group(1).strip() if u else n.group(1).strip()),
                                   interface_ref=i.group(1).strip()))
    return SiteModel(name=site_name, pages=pages)


# ---------------------------------------------------------------------------
# Extended design-object classification (Site/Interface/Integration aren't in
# appian_common's TYPE_HINTS, which is scoped to Rules/Constants/Decisions)
# ---------------------------------------------------------------------------

_kind_cache: dict = {}

def classify_extended(obj: DesignObject) -> str:
    if obj.uuid in _kind_cache:
        return _kind_cache[obj.uuid]
    try:
        text = Path(obj.source_file).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    if re.search(r"<interface|InterfaceDefinition", text, re.IGNORECASE):
        kind = "interface"
    elif re.search(r"<integration|IntegrationDesignObject|connectedSystem", text, re.IGNORECASE):
        kind = "integration"
    elif re.search(r"<constant|ConstantDesignObject", text, re.IGNORECASE):
        kind = "constant"
    else:
        kind = "expression_rule"
    _kind_cache[obj.uuid] = kind
    return kind


UI_ROOT_FUNCS = (
    "a!formLayout", "a!sectionLayout", "a!columnsLayout", "a!cardLayout",
    "a!headerContentLayout", "a!sideBySideLayout", "a!tabsLayout", "a!gridLayout",
    "a!billboardLayout", "a!wizardLayout",
)

def is_ui_returning(obj: DesignObject) -> bool:
    if classify_extended(obj) == "interface":
        return True
    expr = (obj.raw_expression or "").strip()
    return any(expr.startswith(f) for f in UI_ROOT_FUNCS)


# ---------------------------------------------------------------------------
# Minimal SAIL call parser -- scoped to the fairly regular a!func(name: value, ...)
# grammar of UI component calls (NOT a full SAIL grammar -- see top-level plan's
# Phase 1 for that bigger undertaking).
# ---------------------------------------------------------------------------

CALL_START_PATTERN = re.compile(r"^\s*(a!|rule!)([A-Za-z0-9_]+)\s*\(")


def find_matching_paren(text: str, open_idx: int) -> int:
    depth, i, in_str, qc = 0, open_idx, False, ""
    while i < len(text):
        c = text[i]
        if in_str:
            if c == qc and text[i - 1] != "\\":
                in_str = False
        elif c in ('"', "'"):
            in_str, qc = True, c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_top_level(text: str, sep: str = ",") -> list:
    parts, depth, in_str, qc, cur = [], 0, False, "", []
    i = 0
    while i < len(text):
        c = text[i]
        if in_str:
            cur.append(c)
            if c == qc and text[i - 1] != "\\":
                in_str = False
            i += 1
            continue
        if c in ('"', "'"):
            in_str, qc = True, c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def find_top_level_colon(text: str) -> Optional[int]:
    depth, in_str, qc = 0, False, ""
    for i, c in enumerate(text):
        if in_str:
            if c == qc and text[i - 1] != "\\":
                in_str = False
            continue
        if c in ('"', "'"):
            in_str, qc = True, c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ":" and depth == 0:
            return i
    return None


def parse_top_level_call(expr: str):
    """Returns (prefix, name, arg_string) or None."""
    m = CALL_START_PATTERN.match(expr.strip())
    if not m:
        return None
    stripped = expr.strip()
    open_idx = stripped.index("(", m.end(1))
    close_idx = find_matching_paren(stripped, open_idx)
    if close_idx == -1:
        return None
    return m.group(1), m.group(2), stripped[open_idx + 1:close_idx]


def parse_named_args(arg_string: str) -> dict:
    args, positional = {}, []
    for chunk in split_top_level(arg_string, ","):
        idx = find_top_level_colon(chunk)
        if idx is not None:
            args[chunk[:idx].strip()] = chunk[idx + 1:].strip()
        else:
            positional.append(chunk)
    if positional:
        args["_positional"] = positional
    return args


def is_literal_string(val: str) -> bool:
    v = val.strip()
    return len(v) >= 2 and v[0] == v[-1] == '"'


def strip_quotes(val: str) -> str:
    v = val.strip()
    return v[1:-1] if is_literal_string(v) else v


def pascal_case(name: str) -> str:
    parts = re.sub(r"[^A-Za-z0-9]", " ", name).split()
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Unnamed"


def camel_case(name: str) -> str:
    p = pascal_case(name)
    return p[:1].lower() + p[1:] if p else "unnamed"


# ---------------------------------------------------------------------------
# Backend touchpoint ("endpoint") detection
# ---------------------------------------------------------------------------

URL_LITERAL_PATTERN = re.compile(r"https?://[^\s\"'\)]+")


def detect_endpoints(expr: str, ctx: dict) -> None:
    if not expr:
        return
    for m in re.finditer(r"a!queryRecordType\s*\(\s*recordType:\s*([^,\)]+)", expr):
        ctx["endpoints"].append({"kind": "record_query", "source": m.group(1).strip(),
                                  "component": ctx["component_name"]})
    for _ in re.finditer(r"a!queryEntity\s*\(", expr):
        ctx["endpoints"].append({"kind": "record_query_legacy_entity", "component": ctx["component_name"]})
    for m in re.finditer(r"a!startProcess\s*\(\s*processModel:\s*([^,\)]+)", expr):
        ctx["endpoints"].append({"kind": "start_process", "target": m.group(1).strip(),
                                  "component": ctx["component_name"]})
    for m in re.finditer(r"rule!([A-Za-z0-9_]+)", expr):
        obj = ctx["index"].find_by_name(m.group(1))
        if obj and classify_extended(obj) == "integration":
            ctx["endpoints"].append({"kind": "integration_call", "target": m.group(1),
                                      "component": ctx["component_name"]})
    for m in URL_LITERAL_PATTERN.finditer(expr):
        ctx["endpoints"].append({"kind": "literal_url", "url": m.group(0), "component": ctx["component_name"]})


def dedupe_endpoints(endpoints: list) -> list:
    seen, out = set(), []
    for e in endpoints:
        key = json.dumps(e, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Component -> MUI mapping
# ---------------------------------------------------------------------------

# Every MUI symbol this generator can emit, and which package it comes from.
# Used to build correct import statements (MUI ships several sub-packages,
# not just @mui/material) -- see use_import().
MUI_IMPORT_MAP = {
    "Box": "@mui/material", "Paper": "@mui/material", "Grid": "@mui/material", "Stack": "@mui/material",
    "TextField": "@mui/material", "MenuItem": "@mui/material",
    "Checkbox": "@mui/material", "FormControlLabel": "@mui/material", "FormControl": "@mui/material",
    "FormLabel": "@mui/material", "RadioGroup": "@mui/material", "Radio": "@mui/material",
    "Button": "@mui/material", "Typography": "@mui/material", "Card": "@mui/material",
    "CardContent": "@mui/material", "CardHeader": "@mui/material",
    "Tabs": "@mui/material", "Tab": "@mui/material",
    "Stepper": "@mui/material", "Step": "@mui/material", "StepLabel": "@mui/material",
    "Link": "@mui/material",
    "DataGrid": "@mui/x-data-grid",
    "DatePicker": "@mui/x-date-pickers/DatePicker",
}

# a! function -> dispatch "kind" (each kind has its own render_* function below,
# with hardcoded, real MUI component usage rather than a generic tag mapping).
FUNC_TO_KIND = {
    "a!formLayout": "layout_box_form", "a!sectionLayout": "layout_paper",
    "a!columnsLayout": "layout_grid_container", "a!columnLayout": "layout_grid_item",
    "a!cardLayout": "card", "a!headerContentLayout": "layout_box",
    "a!sideBySideLayout": "layout_stack_row", "a!sideBySideItem": "layout_box",
    "a!tabsLayout": "tabs", "a!buttonArrayLayout": "layout_stack_row_buttons",
    "a!textField": "textfield", "a!paragraphField": "textfield_multiline",
    "a!dropdownField": "select", "a!checkboxField": "checkbox",
    "a!radioButtonField": "radiogroup", "a!dateField": "datepicker",
    "a!fileUploadField": "fileupload",
    "a!buttonWidget": "button",
    "a!richTextDisplayField": "typography", "a!gridField": "datagrid",
    "a!imageField": "image", "a!linkField": "link", "a!dynamicLink": "link_button",
    "a!milestoneField": "stepper",
}

# a! functions that return DATA, not UI -- must never be rendered as a JSX tag,
# only as a JS expression (typically a query hitting a REST endpoint post-migration).
DATA_FUNCS = {"queryRecordType", "queryEntity", "queryProcessAnalytics"}


def sail_refs_to_js(text: str) -> str:
    """Rewrite the SAIL reference prefixes that survive drill-down/translation into
    valid JS. pv!/ri! become props.*, local! becomes the matching React state var
    (same camelCase name used when the useState hook was declared), fv! (loop/row
    variable) has its prefix stripped, and any cons! left over (shouldn't normally
    happen -- constants get inlined by the resolver) becomes a Constants.* lookup."""
    text = re.sub(r"\bpv!([A-Za-z0-9_]+)", r"props.\1", text)
    text = re.sub(r"\bri!([A-Za-z0-9_]+)", r"props.\1", text)
    text = re.sub(r"\blocal!([A-Za-z0-9_]+)", lambda m: camel_case(m.group(1)), text)
    text = re.sub(r"\bfv!([A-Za-z0-9_.]+)", lambda m: m.group(1), text)
    text = re.sub(r"\bcons!([A-Za-z0-9_]+)", r"Constants.\1", text)
    # SAIL's text-concatenation operator ' & ' -> JS ' + ' (SAIL has no boolean & operator, so this is safe)
    text = re.sub(r'(?<=[\w"\)\s])&(?=[\s\w"\(])', "+", text)
    return text


def js_translate(expr: str) -> str:
    """Full SAIL -> JS text translation used everywhere a resolved expression is
    emitted into generated code: primitive translation (a!ifThenElse etc, from
    appian_common) plus reference-prefix rewriting above."""
    return sail_refs_to_js(best_effort_translate(expr))


def use_import(ctx: dict, name: str) -> str:
    """Register an MUI symbol as used in the CURRENT component file (ctx['component_name'])
    so write_component_file() can emit the correct `import { X } from '<package>'` line,
    grouped by package. Returns the name unchanged, so it can be used inline:
    `f"<{use_import(ctx, 'Button')}>"`."""
    pkg = MUI_IMPORT_MAP.get(name, "@mui/material")
    ctx.setdefault("imports", {}).setdefault(ctx["component_name"], set()).add((pkg, name))
    return name


def register_handler(ctx: dict, label: str, action_exprs) -> str:
    """Registers (once per component) a stub onClick/onChange handler function for an
    action-bearing prop (saveInto / submit / uri), scanning it for backend touchpoints."""
    name = f"handle{pascal_case(strip_quotes(label)) or 'Action'}"
    comp = ctx["component_name"]
    handlers = ctx.setdefault("handlers", {}).setdefault(comp, [])
    existing = [h.split("(")[0].replace("function ", "") for h in handlers]
    if name not in existing:
        exprs = action_exprs if isinstance(action_exprs, (list, tuple)) else [action_exprs]
        for e in exprs:
            if e:
                detect_endpoints(e, ctx)
        handlers.append(f"function {name}() {{ /* TODO: implement -- see resolution_notes.json "
                         f"for the backend touchpoint(s) this action depends on */ }}")
    return name


def extract_choice_options(args: dict) -> list:
    """Best-effort extraction of a!dropdownField / a!radioButtonField choiceLabels /
    choiceValues into [(label, value), ...]. Only handles the common case where both
    are literal arrays of string/number literals; anything dynamic (rule!/pv!-driven
    choices, which is common for record-backed pickers) is left for manual wiring --
    see the TODO this produces and the developer guide's Known Limitations section."""
    labels_val = args.get("choiceLabels", "")
    values_val = args.get("choiceValues", "")
    if not (labels_val.startswith("{") and labels_val.endswith("}")):
        return []
    if not (values_val.startswith("{") and values_val.endswith("}")):
        return []
    labels = [strip_quotes(e) for e in split_top_level(labels_val[1:-1], ",") if is_literal_string(e)]
    values = [strip_quotes(e) if is_literal_string(e) else e for e in split_top_level(values_val[1:-1], ",")]
    if len(labels) != len(values) or not labels:
        return []
    return list(zip(labels, values))


def render_value(val: str, ctx: dict) -> str:
    val = val.strip()
    if val.startswith("{") and val.endswith("}"):
        elems = split_top_level(val[1:-1], ",")
        return "\n".join(render_value(e, ctx) for e in elems)
    parsed = parse_top_level_call(val)
    if parsed:
        prefix, name, argstr = parsed
        if prefix == "a!" and name in DATA_FUNCS:
            # data function, not a UI component -- render as a JS expression, not JSX
            detect_endpoints(val, ctx)
            return f"{{/* TODO: fetch via REST endpoint -- was {prefix}{name}(...), see endpoints.json */ null}}"
        return render_component(prefix, name, argstr, ctx)
    return render_literal(val, ctx)


def render_literal(val: str, ctx: dict) -> str:
    node = NodeRecord(node_id="literal", node_name="literal", node_type="ui_expression", parameters={"value": val})
    resolved = ctx["resolver"].resolve_node(node)
    p = resolved.parameters["value"]
    ctx["unresolved"].extend(p["unresolved_refs"])
    ctx["warnings"].extend(p["warnings"])
    detect_endpoints(p["resolved_expression"], ctx)
    return "{" + js_translate(p["resolved_expression"]) + "}"


def render_rule_reference(name: str, argstr: str, ctx: dict) -> str:
    obj = ctx["index"].find_by_name(name)
    if obj is None:
        ctx["unresolved"].append(f"rule!{name}")
        return f"<div>{{/* TODO: unresolved interface/rule reference rule!{name} */}}</div>"

    if is_ui_returning(obj):
        component_name = pascal_case(name)
        status = ctx["generated_components"].get(component_name)
        if status == "IN_PROGRESS":
            ctx["warnings"].append(f"circular interface reference at {name} -- rendered as placeholder")
        elif status is None:
            ctx["generated_components"][component_name] = "IN_PROGRESS"
            prev_component = ctx["component_name"]
            ctx["component_name"] = component_name
            child_jsx = render_root_body(obj.raw_expression or "", ctx, component_name)
            write_component_file(ctx["out_dir"], component_name, child_jsx, ctx)
            ctx["component_name"] = prev_component
            ctx["generated_components"][component_name] = "DONE"
        props = parse_named_args(argstr)
        props_jsx = " ".join(f"{camel_case(k)}={render_value(v, ctx)}" for k, v in props.items() if k != "_positional")
        return f"<{component_name} {props_jsx} />".replace("  />", " />")
    else:
        node = NodeRecord(node_id=name, node_name=name, node_type="ui_expression",
                           parameters={"value": obj.raw_expression or ""})
        resolved = ctx["resolver"].resolve_node(node)
        p = resolved.parameters["value"]
        ctx["unresolved"].extend(p["unresolved_refs"])
        ctx["warnings"].extend(p["warnings"])
        detect_endpoints(p["resolved_expression"], ctx)
        detect_endpoints(argstr, ctx)
        return "{" + js_translate(p["resolved_expression"]) + "}"


def render_component(prefix: str, name: str, argstr: str, ctx: dict) -> str:
    if prefix == "rule!":
        return render_rule_reference(name, argstr, ctx)

    func_full = "a!" + name
    args = parse_named_args(argstr)
    kind = FUNC_TO_KIND.get(func_full)
    if not kind:
        detect_endpoints(argstr, ctx)
        children = ""
        for key in ("contents", "items"):
            if key in args:
                children = render_value(args[key], ctx)
        preview = json.dumps({k: v[:60] for k, v in args.items() if k != "_positional"})
        return (f'<div className="appian-unmapped" data-appian-fn="{func_full}">\n'
                f"  {{/* TODO: no mapping for {func_full} -- raw args: {preview} */}}\n{children}\n</div>")
    return COMPONENT_RENDERERS[kind](args, ctx)


# --- layout renderers -------------------------------------------------------

def _children_of(args: dict, ctx: dict) -> str:
    val = args.get("contents") or args.get("items") or args.get("sideBySideItems") or ""
    return render_value(val, ctx) if val else ""


def _optional_label(args: dict, ctx: dict) -> str:
    if "label" not in args or not is_literal_string(args["label"]):
        return ""
    use_import(ctx, "Typography")
    return f'<Typography variant="h6" sx={{{{ mb: 1 }}}}>{strip_quotes(args["label"])}</Typography>\n'


def render_layout_box_form(args, ctx):
    use_import(ctx, "Box")
    return f'<Box component="form" noValidate sx={{{{ display: "flex", flexDirection: "column", gap: 2 }}}}>\n' \
           f'{_optional_label(args, ctx)}{_children_of(args, ctx)}\n</Box>'


def render_layout_paper(args, ctx):
    use_import(ctx, "Paper")
    return f'<Paper variant="outlined" sx={{{{ p: 2, mb: 2 }}}}>\n{_optional_label(args, ctx)}{_children_of(args, ctx)}\n</Paper>'


def render_layout_box(args, ctx):
    use_import(ctx, "Box")
    return f'<Box sx={{{{ mb: 2 }}}}>\n{_optional_label(args, ctx)}{_children_of(args, ctx)}\n</Box>'


def render_layout_grid_container(args, ctx):
    use_import(ctx, "Grid")
    return f'<Grid container spacing={{2}}>\n{_children_of(args, ctx)}\n</Grid>'


def render_layout_grid_item(args, ctx):
    use_import(ctx, "Grid")
    return f'<Grid item xs={{12}} md>\n{_children_of(args, ctx)}\n</Grid>'


def render_layout_stack_row(args, ctx):
    use_import(ctx, "Stack")
    return f'<Stack direction="row" spacing={{2}}>\n{_children_of(args, ctx)}\n</Stack>'


def render_layout_stack_row_buttons(args, ctx):
    use_import(ctx, "Stack")
    return f'<Stack direction="row" spacing={{1}} sx={{{{ mt: 1 }}}}>\n{_children_of(args, ctx)}\n</Stack>'


def render_card(args, ctx):
    use_import(ctx, "Card")
    use_import(ctx, "CardContent")
    header = ""
    if "label" in args and is_literal_string(args["label"]):
        use_import(ctx, "CardHeader")
        header = f'<CardHeader title="{strip_quotes(args["label"])}" />\n'
    return f'<Card variant="outlined">\n{header}<CardContent>\n{_children_of(args, ctx)}\n</CardContent>\n</Card>'


def render_tabs(args, ctx):
    use_import(ctx, "Tabs")
    use_import(ctx, "Tab")
    tabs_val = args.get("tabs", "")
    tab_items = []
    if tabs_val.startswith("{") and tabs_val.endswith("}"):
        for elem in split_top_level(tabs_val[1:-1], ","):
            parsed = parse_top_level_call(elem)
            if parsed and parsed[1] == "tabItem":
                _, _, itemargstr = parsed
                itemargs = parse_named_args(itemargstr)
                lbl = strip_quotes(itemargs.get("label", '"Tab"'))
                body = render_value(itemargs["contents"], ctx) if "contents" in itemargs else ""
                tab_items.append((lbl, body))
    if not tab_items:
        return '<div>{/* TODO: no mappable tabs found in a!tabsLayout(tabs: ...) */}</div>'
    ctx.setdefault("hooks", {}).setdefault(ctx["component_name"], []).append(
        "const [activeTab, setActiveTab] = React.useState(0);")
    tab_headers = "\n".join(f'<Tab label="{l}" />' for l, _ in tab_items)
    tab_panels = "\n".join(f'{{activeTab === {i} && (\n{b}\n)}}' for i, (_, b) in enumerate(tab_items))
    return (f'<>\n<Tabs value={{activeTab}} onChange={{(e, v) => setActiveTab(v)}}>\n{tab_headers}\n</Tabs>\n'
            f'{tab_panels}\n</>')


# --- field / display renderers ----------------------------------------------

def render_textfield(args, ctx, multiline=False):
    use_import(ctx, "TextField")
    props = ['fullWidth']
    if "label" in args:
        lbl = args["label"]
        props.append(f'label="{strip_quotes(lbl)}"' if is_literal_string(lbl) else f"label={render_value(lbl, ctx)}")
    if multiline:
        props.append("multiline minRows={3}")
    if "value" in args:
        props.append(f"value={render_value(args['value'], ctx)}")
    if "placeholder" in args:
        props.append(f'placeholder="{strip_quotes(args["placeholder"])}"')
    if "required" in args:
        props.append(f"required={{{js_translate(args['required'])}}}")
    if "saveInto" in args:
        handler = register_handler(ctx, args.get("label", "Field"), args["saveInto"])
        props.append(f"onChange={{(e) => {handler}(e.target.value)}}")
    return f"<TextField {' '.join(props)} />"


def render_textfield_multiline(args, ctx):
    return render_textfield(args, ctx, multiline=True)


def render_select(args, ctx):
    use_import(ctx, "TextField")
    use_import(ctx, "MenuItem")
    label = strip_quotes(args.get("label", '""'))
    options = extract_choice_options(args)
    value_jsx = render_value(args["value"], ctx) if "value" in args else '""'
    items = ("\n".join(f'<MenuItem value="{v}">{l}</MenuItem>' for l, v in options)
             if options else '{/* TODO: map choiceLabels/choiceValues (or record-backed choices) to <MenuItem> options */}')
    return f'<TextField select fullWidth label="{label}" value={value_jsx}>\n{items}\n</TextField>'


def render_checkbox(args, ctx):
    use_import(ctx, "FormControlLabel")
    use_import(ctx, "Checkbox")
    label = strip_quotes(args.get("label", '""'))
    checked = f"checked={render_value(args['value'], ctx)}" if "value" in args else ""
    return f'<FormControlLabel control={{<Checkbox {checked} />}} label="{label}" />'


def render_radiogroup(args, ctx):
    use_import(ctx, "FormControl")
    use_import(ctx, "FormLabel")
    use_import(ctx, "RadioGroup")
    use_import(ctx, "FormControlLabel")
    use_import(ctx, "Radio")
    label = strip_quotes(args.get("label", '""'))
    options = extract_choice_options(args)
    items = ("\n".join(f'<FormControlLabel value="{v}" control={{<Radio />}} label="{l}" />' for l, v in options)
              if options else '{/* TODO: map choiceLabels/choiceValues */}')
    return f'<FormControl>\n<FormLabel>{label}</FormLabel>\n<RadioGroup>\n{items}\n</RadioGroup>\n</FormControl>'


def render_datepicker(args, ctx):
    use_import(ctx, "DatePicker")
    label = strip_quotes(args.get("label", '""'))
    value = render_value(args["value"], ctx) if "value" in args else "null"
    return (f'{{/* NOTE: requires <LocalizationProvider> at the app root -- see developer guide */}}\n'
            f'<DatePicker label="{label}" value={value} '
            f'onChange={{(newValue) => {{/* TODO: wire onChange -> saveInto */}}}} />')


def render_fileupload(args, ctx):
    use_import(ctx, "Button")
    label = strip_quotes(args.get("label", '"Upload"'))
    return (f'<Button variant="outlined" component="label">\n  {label}\n'
            f'  <input type="file" hidden onChange={{(e) => {{/* TODO: handle file upload -> saveInto */}}}} />\n'
            f'</Button>')


def render_button(args, ctx):
    use_import(ctx, "Button")
    label = strip_quotes(args.get("label", '"Action"'))
    handler = register_handler(ctx, label, [args.get("saveInto", ""), args.get("submit", "")])
    return f'<Button variant="contained" onClick={{{handler}}}>{label}</Button>'


def render_typography(args, ctx):
    use_import(ctx, "Typography")
    value_jsx = render_value(args.get("value", '""'), ctx)
    return f"<Typography>{value_jsx}</Typography>"


def render_image(args, ctx):
    src = args.get("image", args.get("source", '""'))
    alt = strip_quotes(args.get("altText", '""'))
    return f'<img src={render_value(src, ctx)} alt="{alt}" style={{{{ maxWidth: "100%" }}}} />'


def render_link(args, ctx):
    use_import(ctx, "Link")
    label = strip_quotes(args.get("label", '"Link"'))
    if "uri" in args:
        uri = args["uri"]
        if is_literal_string(uri):
            href = f'"{strip_quotes(uri)}"'
        else:
            detect_endpoints(uri, ctx)
            href = '"#"'
        return f'<Link href={href}>{label}</Link>'
    handler = register_handler(ctx, label, args.get("saveInto", ""))
    return f'<Link component="button" onClick={{{handler}}}>{label}</Link>'


def render_link_button(args, ctx):
    """a!dynamicLink -- almost always action-triggering rather than a plain href."""
    use_import(ctx, "Link")
    label = strip_quotes(args.get("label", '"Link"'))
    handler = register_handler(ctx, label, [args.get("saveInto", ""), args.get("uri", "")])
    return f'<Link component="button" onClick={{{handler}}}>{label}</Link>'


def render_stepper(args, ctx):
    use_import(ctx, "Stepper")
    use_import(ctx, "Step")
    use_import(ctx, "StepLabel")
    steps_val = args.get("steps", args.get("milestones", ""))
    step_items = []
    if steps_val.startswith("{") and steps_val.endswith("}"):
        for elem in split_top_level(steps_val[1:-1], ","):
            label = strip_quotes(elem) if is_literal_string(elem) else render_value(elem, ctx)
            step_items.append(f'<Step><StepLabel>{label}</StepLabel></Step>')
    body = "\n".join(step_items) if step_items else '{/* TODO: map milestone steps */}'
    active = js_translate(args["activeStep"]) if "activeStep" in args else "0"
    return f'<Stepper activeStep={{{active}}}>\n{body}\n</Stepper>'


def render_datagrid(args, ctx):
    use_import(ctx, "DataGrid")
    source_jsx = render_value(args.get("data", args.get("value", '""')), ctx)
    columns_val = args.get("columns", "")
    col_defs = []
    if columns_val.startswith("{") and columns_val.endswith("}"):
        for elem in split_top_level(columns_val[1:-1], ","):
            parsed = parse_top_level_call(elem)
            if parsed:
                _, _, colargstr = parsed
                colargs = parse_named_args(colargstr)
                header = strip_quotes(colargs.get("label", '""'))
                field = camel_case(header) or f"col{len(col_defs)}"
                value_expr = colargs.get("value", colargs.get("field", ""))
                # Inside a!gridColumn's `value`, fv!<rowVar>.field refers to the current row
                # (Appian's grid-loop variable) -- MUI's valueGetter receives (params) with the
                # row under params.row, so route that case through params.row.* specifically,
                # rather than the generic fv! stripping sail_refs_to_js does everywhere else.
                grid_expr = re.sub(r"\bfv!\w+\.", "params.row.", value_expr)
                col_defs.append(f'{{ field: "{field}", headerName: "{header}", flex: 1, '
                                 f'valueGetter: (params) => {js_translate(grid_expr)} }}')
    columns_js = "[\n    " + ",\n    ".join(col_defs) + "\n  ]" if col_defs else "[] /* TODO: map grid columns */"
    return (f'<div style={{{{ height: 400, width: "100%" }}}}>\n'
            f'  {{/* NOTE: assumes each row has an "id" field -- MUI DataGrid requires getRowId or row.id */}}\n'
            f'  <DataGrid rows={source_jsx} columns={{{columns_js}}} />\n'
            f'</div>')


COMPONENT_RENDERERS = {
    "layout_box_form": render_layout_box_form, "layout_paper": render_layout_paper,
    "layout_box": render_layout_box, "layout_grid_container": render_layout_grid_container,
    "layout_grid_item": render_layout_grid_item, "layout_stack_row": render_layout_stack_row,
    "layout_stack_row_buttons": render_layout_stack_row_buttons, "card": render_card, "tabs": render_tabs,
    "textfield": render_textfield, "textfield_multiline": render_textfield_multiline,
    "select": render_select, "checkbox": render_checkbox, "radiogroup": render_radiogroup,
    "datepicker": render_datepicker, "fileupload": render_fileupload, "button": render_button,
    "typography": render_typography, "image": render_image, "link": render_link,
    "link_button": render_link_button, "stepper": render_stepper, "datagrid": render_datagrid,
}


def render_root_body(expr: str, ctx: dict, component_name: str) -> str:
    parsed = parse_top_level_call(expr)
    if parsed and parsed[0] == "a!" and parsed[1] == "localVariables":
        _, _, argstr = parsed
        chunks = split_top_level(argstr, ",")
        hooks, body_expr = [], None
        for chunk in chunks:
            idx = find_top_level_colon(chunk)
            if idx is not None and chunk[:idx].strip().startswith("local!"):
                lname = chunk[:idx].strip()[6:]
                lval = chunk[idx + 1:].strip()
                node = NodeRecord(node_id=lname, node_name=lname, node_type="ui_expression", parameters={"value": lval})
                resolved = ctx["resolver"].resolve_node(node)
                p = resolved.parameters["value"]
                detect_endpoints(p["resolved_expression"], ctx)
                js = js_translate(p["resolved_expression"])
                hooks.append(f"const [{camel_case(lname)}, set{pascal_case(lname)}] = React.useState({js});")
            else:
                body_expr = chunk
        ctx.setdefault("hooks", {})[component_name] = hooks
        return render_value(body_expr, ctx) if body_expr else "<div>{/* empty body */}</div>"
    return render_value(expr, ctx)


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_component_file(out_dir: Path, name: str, body_jsx: str, ctx: dict) -> None:
    # MUI imports actually used, grouped by package (registered via use_import() during rendering)
    used = ctx.get("imports", {}).get(name, set())
    by_package: dict = {}
    for pkg, sym in used:
        by_package.setdefault(pkg, set()).add(sym)

    # Anything else capitalized in the JSX that ISN'T an MUI symbol is a locally-generated
    # child component (a drilled-into interface that returned UI) -- import from './Name'.
    all_capitalized_tags = set(re.findall(r"<([A-Z][A-Za-z0-9]*)", body_jsx))
    mui_symbols_used = {sym for syms in by_package.values() for sym in syms}
    child_tags = sorted(all_capitalized_tags - mui_symbols_used - {name})

    lines = ["import React from 'react';"]
    for pkg in sorted(by_package):
        lines.append(f"import {{ {', '.join(sorted(by_package[pkg]))} }} from '{pkg}';")
    for c in child_tags:
        lines.append(f"import {c} from './{c}';")
    lines.append("")
    for h in ctx.get("handlers", {}).get(name, []):
        lines.append(h)
    if ctx.get("handlers", {}).get(name):
        lines.append("")
    lines.append(f"export default function {name}(props) {{")
    for h in ctx.get("hooks", {}).get(name, []):
        lines.append(f"  {h}")
    lines.append("  return (")
    for l in body_jsx.splitlines():
        lines.append("    " + l)
    lines.append("  );")
    lines.append("}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.jsx").write_text("\n".join(lines), encoding="utf-8")


def write_app_jsx(out_dir: Path, routes: list) -> None:
    lines = ["import React from 'react';", "import { BrowserRouter, Routes, Route } from 'react-router-dom';"]
    for _, comp in routes:
        lines.append(f"import {comp} from './{comp}';")
    lines += ["", "export default function App() {", "  return (", "    <BrowserRouter>", "      <Routes>"]
    for stub, comp in routes:
        path = stub if stub.startswith("/") else "/" + stub
        lines.append(f'        <Route path="{path}" element={{<{comp} />}} />')
    lines += ["      </Routes>", "    </BrowserRouter>", "  );", "}"]
    (out_dir / "App.jsx").write_text("\n".join(lines), encoding="utf-8")


def write_reports(out_dir: Path, endpoints: list, unresolved: list, warnings: list) -> None:
    (out_dir / "endpoints.json").write_text(json.dumps(dedupe_endpoints(endpoints), indent=2), encoding="utf-8")
    (out_dir / "resolution_notes.json").write_text(
        json.dumps({"unresolved_references": sorted(set(unresolved)), "warnings": sorted(set(warnings))}, indent=2),
        encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def generate_site(index: DesignObjectIndex, site: SiteModel, out_dir: Path) -> None:
    resolver = DrillDownResolver(index)
    ctx = {
        "index": index, "resolver": resolver, "generated_components": {}, "endpoints": [],
        "unresolved": [], "warnings": [], "out_dir": out_dir, "component_name": "", "handlers": {}, "hooks": {},
    }
    routes = []
    for page in site.pages:
        obj = index.find_by_name(page.interface_ref)
        if obj is None:
            ctx["unresolved"].append(f"page:{page.name} -> interface:{page.interface_ref}")
            print(f"[warn] page '{page.name}' references unresolved interface '{page.interface_ref}'", file=sys.stderr)
            continue
        component_name = pascal_case(page.name)
        ctx["component_name"] = component_name
        ctx["generated_components"][component_name] = "IN_PROGRESS"
        body = render_root_body(obj.raw_expression or "", ctx, component_name)
        write_component_file(out_dir, component_name, body, ctx)
        ctx["generated_components"][component_name] = "DONE"
        routes.append((page.url_stub, component_name))
        print(f"[generate] page '{page.name}' -> {component_name}.jsx (route {page.url_stub})")

    write_app_jsx(out_dir, routes)
    write_reports(out_dir, ctx["endpoints"], ctx["unresolved"], ctx["warnings"])
    print(f"[generate] wrote App.jsx, endpoints.json, resolution_notes.json to {out_dir}")
    if ctx["endpoints"]:
        print(f"[generate] {len(dedupe_endpoints(ctx['endpoints']))} backend touchpoint(s) found -- see endpoints.json")
    if ctx["unresolved"]:
        print(f"[generate] {len(set(ctx['unresolved']))} unresolved reference(s) -- see resolution_notes.json")


def inspect_file(path: Path) -> None:
    idx = DesignObjectIndex(path.parent)
    obj = idx._parse_object_file(path)
    if obj is None:
        print(f"Could not detect a <name> element in {path}. Adjust DesignObjectIndex in appian_common.py.")
        return
    from dataclasses import asdict
    print(json.dumps(asdict(obj), indent=2)[:3000])
    print(f"\nextended kind: {classify_extended(obj)}")
    print("If this looks wrong, adjust classify_extended() in this file (Site/Interface/Integration "
          "detection is separate from appian_common.py's TYPE_HINTS, which only covers Rules/Constants/Decisions).")


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------

def run_demo(out_dir: Path) -> None:
    demo_root = out_dir / "_demo_export"
    demo_root.mkdir(parents=True, exist_ok=True)

    (demo_root / "MinimumBalance.xml").write_text("""
    <constant uuid="c-0001"><name>MinimumBalance</name><value>500</value></constant>
    """, encoding="utf-8")

    (demo_root / "isAccountEligible.xml").write_text("""
    <expressionRule uuid="r-0002">
      <name>isAccountEligible</name>
      <definition>
        a!ifThenElse(condition: ri!balance &gt;= cons!MinimumBalance, thenValue: true, elseValue: false)
      </definition>
    </expressionRule>
    """, encoding="utf-8")

    (demo_root / "CreditCheckIntegration.xml").write_text("""
    <integration uuid="i-0003"><name>CreditCheckIntegration</name></integration>
    """, encoding="utf-8")

    (demo_root / "accountStatusBadge.xml").write_text("""
    <interface uuid="int-0004">
      <name>accountStatusBadge</name>
      <definition>
        a!richTextDisplayField(
          label: "Status",
          value: rule!isAccountEligible(balance: ri!balance)
        )
      </definition>
    </interface>
    """, encoding="utf-8")

    (demo_root / "accountDetailsInterface.xml").write_text("""
    <interface uuid="int-0005">
      <name>accountDetailsInterface</name>
      <definition>
        a!localVariables(
          local!balance: pv!currentBalance,
          a!formLayout(
            label: "Account Details",
            contents: {
              a!textField(label: "Account Id", value: pv!accountId, placeholder: "e.g. 100234"),
              rule!accountStatusBadge(balance: local!balance),
              a!gridField(
                label: "Recent Transactions",
                data: a!queryRecordType(recordType: "Transaction", filters: pv!accountId),
                columns: {
                  a!gridColumn(label: "Date", value: fv!row.date),
                  a!gridColumn(label: "Amount", value: fv!row.amount)
                }
              ),
              a!buttonWidget(label: "Run Credit Check", submit: rule!CreditCheckIntegration()),
              a!buttonWidget(label: "Start Approval", saveInto: a!startProcess(processModel: "Loan Approval Subprocess")),
              a!linkField(label: "Bank Policies", uri: "https://intranet.example.com/policies")
            }
          )
        )
      </definition>
    </interface>
    """, encoding="utf-8")

    (demo_root / "newAccountFormInterface.xml").write_text("""
    <interface uuid="int-0006">
      <name>newAccountFormInterface</name>
      <definition>
        a!formLayout(
          label: "Open New Account",
          contents: {
            a!dropdownField(
              label: "Account Type",
              value: pv!selectedType,
              choiceLabels: {"Checking", "Savings"},
              choiceValues: {"CHECKING", "SAVINGS"}
            ),
            a!checkboxField(label: "Enroll in paperless statements", value: pv!paperless),
            a!tabsLayout(
              tabs: {
                a!tabItem(label: "Personal Info", contents: {a!textField(label: "Full Name", value: pv!fullName)}),
                a!tabItem(label: "Funding", contents: {a!textField(label: "Initial Deposit", value: pv!deposit)})
              }
            ),
            a!buttonWidget(label: "Submit Application", submit: a!startProcess(processModel: "Open Account Process"))
          }
        )
      </definition>
    </interface>
    """, encoding="utf-8")

    site = SiteModel(name="Customer Portal", pages=[
        SitePage(name="Account Details", url_stub="account-details", interface_ref="accountDetailsInterface"),
        SitePage(name="New Account", url_stub="new-account", interface_ref="newAccountFormInterface"),
    ])

    index = DesignObjectIndex(demo_root)
    index.build()
    converted_dir = out_dir / "converted"
    generate_site(index, site, converted_dir)

    print("\n--- Demo: AccountDetails.jsx ---")
    print((converted_dir / "AccountDetails.jsx").read_text())
    print("\n--- Demo: AccountStatusBadge.jsx (nested interface -> its own component) ---")
    print((converted_dir / "AccountStatusBadge.jsx").read_text())
    print("\n--- Demo: NewAccount.jsx (dropdown / checkbox / tabs) ---")
    print((converted_dir / "NewAccount.jsx").read_text())
    print("\n--- Demo: endpoints.json ---")
    print((converted_dir / "endpoints.json").read_text())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-root", type=Path, help="Path to local Appian design object export directory")
    parser.add_argument("--site-report", type=Path, help="Path to a JSON report describing the site's pages")
    parser.add_argument("--out-dir", type=Path, default=Path("./react_output"), help="Output directory")
    parser.add_argument("--inspect", type=Path, help="Dump parsed fields for one exported XML file")
    parser.add_argument("--demo", action="store_true", help="Run a self-contained demo with synthetic data")
    args = parser.parse_args()

    if args.demo:
        run_demo(args.out_dir)
        return
    if args.inspect:
        inspect_file(args.inspect)
        return
    if not (args.export_root and args.site_report):
        parser.error("--export-root and --site-report are required (or use --demo / --inspect)")

    index = DesignObjectIndex(args.export_root)
    index.build()
    site = load_site_from_report(args.site_report)
    generate_site(index, site, args.out_dir)


if __name__ == "__main__":
    main()
