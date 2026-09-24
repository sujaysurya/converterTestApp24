#!/usr/bin/env python3
"""
appian_script_task_converter.py
================================

Phase 1 tool for the Appian -> Java migration: converts a single Script Task
node's SAIL expression into a Java method scaffold, drilling down through any
rule!/cons! references it uses until only primitive SAIL remains (no further
calls to Appian design objects).

WHAT THIS SCRIPT DOES
----------------------
1. Indexes your locally exported design objects (Expression Rules, Constants,
   Decisions) so any object can be looked up by name or UUID.
2. Extracts every rule!/cons!/pv!/ri!/local! reference from a node's SAIL
   expression.
3. Recursively resolves rule! and cons! references against the local index,
   substituting each one's own body/value inline, until the resolved tree
   contains no further rule!/cons! calls (cycle-safe, depth-limited, memoized).
4. Produces:
     - a JSON "resolution report" (full audit trail: what was drilled into,
       what's unresolved, what needs manual attention)
     - a Java method scaffold with the fully-resolved SAIL logic embedded as
       an annotated comment block, plus a best-effort translation for a small
       set of very common primitives (a!ifThenElse, concat, basic comparisons).

WHAT THIS SCRIPT DELIBERATELY DOES NOT DO (yet)
-------------------------------------------------
Full SAIL -> Java transpilation requires a real SAIL grammar/parser (this was
flagged as its own phase in the top-level plan). Hand-rolling that here would
be guesswork dressed up as certainty. Instead, this script guarantees the hard
part -- "did we find and inline every dependency, with nothing left unresolved
that shouldn't be" -- and hands off a clean, fully-resolved expression for
either (a) a human dev, or (b) a follow-on LLM-assisted translation pass, to
finish turning into idiomatic Java. That follow-on pass is a natural Phase 1b
script once you're happy with this drill-down behavior.

IMPORTANT -- ADAPT THE PARSING LAYER TO YOUR EXPORT
------------------------------------------------------
I don't have verified knowledge of your exact export file layout (Appian's
export XML schema is not public and varies by version/config). The
`DesignObjectIndex._parse_object_file()` method below uses permissive,
regex-based extraction (looks for the object's <name>, a `type` marker, and
raw SAIL text) rather than assuming exact XML tags/namespaces. Run this script
with `--inspect <path-to-one-xml-file>` first -- it will dump what it detected
-- and adjust the regexes/XPath in that one method to match your real files.
Everything downstream (drill-down, Java generation) is independent of that
detail and needs no changes.

USAGE
-----
Demo (no real data needed, proves the pipeline works end-to-end):
    python appian_script_task_converter.py --demo

Real run:
    python appian_script_task_converter.py \\
        --export-root /path/to/local/appian_export \\
        --node-report /path/to/process_model_report.json \\
        --node-id <script-task-node-id> \\
        --out-dir ./converted

Inspect one exported object file to calibrate the parser:
    python appian_script_task_converter.py --inspect /path/to/some_rule.xml
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DesignObject:
    uuid: str
    name: str
    obj_type: str                  # 'expression_rule' | 'constant' | 'decision' | 'unknown'
    raw_expression: Optional[str]  # SAIL body, for rules/decisions
    value: Optional[str]           # literal value, for constants
    source_file: str


@dataclass
class NodeRecord:
    node_id: str
    node_name: str
    node_type: str                 # e.g. 'ScriptTask'
    expression: str                # raw SAIL for this node's logic
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)


@dataclass
class ResolvedNode:
    node_id: str
    node_name: str
    node_type: str
    raw_expression: str
    resolved_expression: str
    referenced_design_objects: list = field(default_factory=list)   # names drilled into
    unresolved_refs: list = field(default_factory=list)              # rule!/cons! not found locally
    dynamic_refs: list = field(default_factory=list)                 # refs that couldn't be statically resolved
    max_depth_reached: int = 0
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Design object indexing
# ---------------------------------------------------------------------------

class DesignObjectIndex:
    """
    Scans a local Appian export directory and builds name/uuid lookups for
    Expression Rules, Decisions and Constants.

    ADAPT THIS CLASS to your actual export layout -- see module docstring.
    The permissive regex approach below works reasonably well against raw
    Appian design-object XML (which typically contains a readable <name>
    element and the SAIL body inside a text/CDATA blob) but you should verify
    against `--inspect` output on a real file before trusting it at scale.
    """

    NAME_PATTERN = re.compile(r"<name[^>]*>([^<]+)</name>", re.IGNORECASE)
    UUID_PATTERN = re.compile(r'uuid=["\']([a-f0-9\-]{20,})["\']', re.IGNORECASE)
    # Heuristic type detection based on filename / tag hints -- adjust as needed.
    TYPE_HINTS = {
        "expression_rule": [r"expressionRule", r"<sail", r"ExpressionRule"],
        "decision": [r"DecisionFunction", r"decisionTable"],
        "constant": [r"<constant", r"ConstantDesignObject"],
    }
    # SAIL body is commonly wrapped in a definition/expression tag. Widen this
    # list if your export uses different tag names.
    EXPR_BODY_PATTERNS = [
        re.compile(r"<definition[^>]*>(.*?)</definition>", re.IGNORECASE | re.DOTALL),
        re.compile(r"<expression[^>]*>(.*?)</expression>", re.IGNORECASE | re.DOTALL),
        re.compile(r"<value[^>]*>(.*?)</value>", re.IGNORECASE | re.DOTALL),
    ]

    def __init__(self, export_root: Path):
        self.export_root = export_root
        self.by_name: dict[str, DesignObject] = {}
        self.by_uuid: dict[str, DesignObject] = {}

    def build(self) -> None:
        if not self.export_root.exists():
            raise FileNotFoundError(f"Export root not found: {self.export_root}")
        xml_files = list(self.export_root.rglob("*.xml"))
        for f in xml_files:
            obj = self._parse_object_file(f)
            if obj is None:
                continue
            self.by_name[obj.name] = obj
            self.by_uuid[obj.uuid] = obj
        print(f"[index] parsed {len(self.by_name)} design objects from "
              f"{len(xml_files)} xml files under {self.export_root}", file=sys.stderr)

    def _parse_object_file(self, path: Path) -> Optional[DesignObject]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

        name_match = self.NAME_PATTERN.search(text)
        if not name_match:
            return None
        name = name_match.group(1).strip()

        uuid_match = self.UUID_PATTERN.search(text)
        uuid = uuid_match.group(1) if uuid_match else path.stem

        obj_type = "unknown"
        for t, patterns in self.TYPE_HINTS.items():
            if any(re.search(p, text, re.IGNORECASE) for p in patterns):
                obj_type = t
                break

        raw_expression = None
        for pattern in self.EXPR_BODY_PATTERNS:
            m = pattern.search(text)
            if m:
                raw_expression = self._unescape(m.group(1).strip())
                break

        value = None
        if obj_type == "constant" and raw_expression:
            value = raw_expression

        return DesignObject(
            uuid=uuid, name=name, obj_type=obj_type,
            raw_expression=raw_expression, value=value, source_file=str(path),
        )

    @staticmethod
    def _unescape(text: str) -> str:
        return (text.replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&amp;", "&").replace("&quot;", '"')
                    .replace("<![CDATA[", "").replace("]]>", ""))

    def find_by_name(self, name: str) -> Optional[DesignObject]:
        return self.by_name.get(name)


# ---------------------------------------------------------------------------
# 2. SAIL reference extraction
# ---------------------------------------------------------------------------

REF_PATTERN = re.compile(r"\b(rule|cons|ri|pv|local|fv)!([A-Za-z0-9_]+)")
# A "dynamic" ref is one built from concatenation/expressions rather than a
# literal name -- these can't be statically resolved and must be flagged.
DYNAMIC_RULE_PATTERN = re.compile(r"rule!\s*\(")


def extract_refs(expr: str) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {"rule": set(), "cons": set(), "ri": set(), "pv": set(), "local": set(), "fv": set()}
    for prefix, name in REF_PATTERN.findall(expr):
        refs[prefix].add(name)
    return refs


# ---------------------------------------------------------------------------
# 3. Recursive drill-down resolver
# ---------------------------------------------------------------------------

class DrillDownResolver:
    """
    Recursively inlines rule!/cons! references until the expression contains
    none left that are resolvable against the local index. Memoized (a rule
    reused across 50 nodes is only resolved once), cycle-safe, depth-limited.
    """

    def __init__(self, index: DesignObjectIndex, max_depth: int = 15):
        self.index = index
        self.max_depth = max_depth
        self._resolved_cache: dict[str, str] = {}
        self._visiting: set[str] = set()

    def resolve_node(self, node: NodeRecord) -> ResolvedNode:
        result = ResolvedNode(
            node_id=node.node_id, node_name=node.node_name, node_type=node.node_type,
            raw_expression=node.expression, resolved_expression=node.expression,
        )
        self._visiting.clear()
        resolved_expr, depth = self._resolve_expression(
            node.expression, depth=0, result=result,
        )
        result.resolved_expression = resolved_expr
        result.max_depth_reached = depth
        return result

    def _resolve_expression(self, expr: str, depth: int, result: ResolvedNode) -> tuple[str, int]:
        if depth > self.max_depth:
            result.warnings.append(f"max_depth ({self.max_depth}) exceeded -- stopped drilling further")
            return expr, depth

        if DYNAMIC_RULE_PATTERN.search(expr):
            result.dynamic_refs.append("rule!(...) built dynamically -- cannot statically resolve, flag for manual review")

        refs = extract_refs(expr)
        max_child_depth = depth
        expr_out = expr

        for name in sorted(refs["rule"] | refs["cons"]):
            key_prefix = "rule" if name in refs["rule"] else "cons"
            cache_key = f"{key_prefix}!{name}"

            if cache_key in self._visiting:
                result.warnings.append(f"circular reference detected at {cache_key} -- left as call, not inlined")
                continue

            if cache_key in self._resolved_cache:
                inlined = self._resolved_cache[cache_key]
            else:
                obj = self.index.find_by_name(name)
                if obj is None:
                    result.unresolved_refs.append(cache_key)
                    continue

                result.referenced_design_objects.append(f"{obj.obj_type}:{obj.name}")

                if obj.obj_type == "constant":
                    inlined = obj.value if obj.value is not None else f"/* unresolved constant value: {name} */"
                    child_depth = depth + 1
                else:
                    body = obj.raw_expression or f"/* no expression body found for {name} */"
                    self._visiting.add(cache_key)
                    inlined, child_depth = self._resolve_expression(body, depth + 1, result)
                    self._visiting.discard(cache_key)

                self._resolved_cache[cache_key] = inlined
                max_child_depth = max(max_child_depth, child_depth)

            # Substitute every occurrence of this specific reference, wrapped
            # so the provenance stays visible in the resolved output.
            call_pattern = re.compile(rf"\b{key_prefix}!{re.escape(name)}\b(\([^)]*\))?")
            expr_out = call_pattern.sub(
                f"/* inlined {key_prefix}!{name} */ ({inlined})", expr_out,
            )

        return expr_out, max_child_depth


# ---------------------------------------------------------------------------
# 4. Java scaffold generation
# ---------------------------------------------------------------------------

# Very small, deliberately conservative set of primitive-SAIL -> Java
# translations for the most common patterns. Anything not covered here is
# left as a commented TODO rather than mistranslated.
_SIMPLE_TRANSLATIONS = [
    (re.compile(r"a!ifThenElse\s*\(\s*condition:\s*(.*?),\s*thenValue:\s*(.*?),\s*elseValue:\s*(.*?)\)", re.DOTALL),
     r"(\1) ? (\2) : (\3)"),
    (re.compile(r"\bconcat\s*\("), "String.join(\"\", "),
    (re.compile(r"\band\("), "allMatch("),
    (re.compile(r"\bor\("), "anyMatch("),
]


def java_identifier(name: str) -> str:
    """camelCase-safe identifier: strips illegal chars, lowercases each
    underscore-separated word's leading letter is left alone (so
    'Evaluate_Account_Eligibility' -> 'evaluate_account_eligibility' style
    file-safe name); used for both method names and output filenames."""
    ident = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")
    if not ident:
        return "unnamed"
    if ident[0].isdigit():
        ident = "_" + ident
    return ident.lower()


def best_effort_translate(expr: str) -> str:
    out = expr
    for pattern, replacement in _SIMPLE_TRANSLATIONS:
        out = pattern.sub(replacement, out)
    return out


def generate_java_scaffold(resolved: ResolvedNode, node: NodeRecord) -> str:
    method_name = java_identifier(resolved.node_name or resolved.node_id)
    params = ", ".join(f"Object {java_identifier(p)}" for p in node.inputs) or ""
    translated = best_effort_translate(resolved.resolved_expression)

    lines = []
    lines.append(f"// Generated scaffold for Script Task node: {resolved.node_id} ({resolved.node_name})")
    lines.append(f"// Drilled into {len(resolved.referenced_design_objects)} design object(s): "
                  f"{', '.join(resolved.referenced_design_objects) or 'none'}")
    if resolved.unresolved_refs:
        lines.append(f"// WARNING: unresolved references (not found in local export): "
                      f"{', '.join(resolved.unresolved_refs)}")
    if resolved.dynamic_refs:
        lines.append(f"// WARNING: dynamic references requiring manual review: "
                      f"{'; '.join(resolved.dynamic_refs)}")
    if resolved.warnings:
        for w in resolved.warnings:
            lines.append(f"// WARNING: {w}")
    lines.append(f"public Object {method_name}({params}) {{")
    lines.append(f"    // --- fully drilled-down SAIL logic (primitive-level) ---")
    for l in translated.strip().splitlines() or [translated]:
        lines.append(f"    // {l}")
    lines.append(f"    // TODO: finish translating the above into real Java statements.")
    lines.append(f"    // Only common primitives (a!ifThenElse, concat, and/or) were auto-translated;")
    lines.append(f"    // everything else was left as an annotated comment for a follow-on pass.")
    lines.append(f"    throw new UnsupportedOperationException(\"not yet translated: {method_name}\");")
    lines.append(f"}}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Orchestration
# ---------------------------------------------------------------------------

def convert_node(index: DesignObjectIndex, node: NodeRecord, out_dir: Path) -> None:
    resolver = DrillDownResolver(index)
    resolved = resolver.resolve_node(node)

    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / f"{node.node_id}_resolution.json"
    report_path.write_text(json.dumps(asdict(resolved), indent=2), encoding="utf-8")

    java_path = out_dir / f"{java_identifier(node.node_name or node.node_id)}.java.txt"
    java_path.write_text(generate_java_scaffold(resolved, node), encoding="utf-8")

    print(f"[convert] node {node.node_id} -> {report_path.name}, {java_path.name}")
    print(f"          referenced: {resolved.referenced_design_objects}")
    if resolved.unresolved_refs:
        print(f"          UNRESOLVED: {resolved.unresolved_refs}")


def load_node_from_report(node_report_path: Path, node_id: str) -> NodeRecord:
    """
    ADAPT THIS to your actual per-process-model report JSON schema. Assumes a
    report shaped like: {"nodes": [{"id":..., "name":..., "type":...,
    "expression":..., "inputs":[...], "outputs":[...]}, ...]}
    """
    data = json.loads(node_report_path.read_text(encoding="utf-8"))
    for n in data.get("nodes", []):
        if n.get("id") == node_id:
            return NodeRecord(
                node_id=n["id"], node_name=n.get("name", n["id"]),
                node_type=n.get("type", "ScriptTask"),
                expression=n.get("expression", ""),
                inputs=n.get("inputs", []), outputs=n.get("outputs", []),
            )
    raise KeyError(f"node id {node_id} not found in {node_report_path}")


def inspect_file(path: Path) -> None:
    idx = DesignObjectIndex(path.parent)
    obj = idx._parse_object_file(path)
    if obj is None:
        print(f"Could not detect a <name> element in {path}. "
              f"Open the file and adjust DesignObjectIndex.NAME_PATTERN / EXPR_BODY_PATTERNS.")
        return
    print(json.dumps(asdict(obj), indent=2)[:3000])
    print("\nIf obj_type/raw_expression look wrong, adjust TYPE_HINTS / "
          "EXPR_BODY_PATTERNS in DesignObjectIndex to match this file's real tags.")


# ---------------------------------------------------------------------------
# Demo mode -- synthetic data, proves the pipeline end-to-end with no real files
# ---------------------------------------------------------------------------

def run_demo(out_dir: Path) -> None:
    demo_root = out_dir / "_demo_export"
    demo_root.mkdir(parents=True, exist_ok=True)

    (demo_root / "MinimumBalance.xml").write_text("""
    <constant uuid="c-0001">
      <name>MinimumBalance</name>
      <value>500</value>
    </constant>
    """, encoding="utf-8")

    (demo_root / "isAccountEligible.xml").write_text("""
    <expressionRule uuid="r-0002">
      <name>isAccountEligible</name>
      <definition>
        a!ifThenElse(
          condition: ri!balance &gt;= cons!MinimumBalance,
          thenValue: true,
          elseValue: false
        )
      </definition>
    </expressionRule>
    """, encoding="utf-8")

    (demo_root / "formatAccountSummary.xml").write_text("""
    <expressionRule uuid="r-0003">
      <name>formatAccountSummary</name>
      <definition>
        concat("Account ", ri!accountId, " eligible: ", rule!isAccountEligible(balance: ri!balance))
      </definition>
    </expressionRule>
    """, encoding="utf-8")

    node = NodeRecord(
        node_id="node-42",
        node_name="Evaluate Account Eligibility",
        node_type="ScriptTask",
        expression="rule!formatAccountSummary(accountId: pv!accountId, balance: pv!currentBalance)",
        inputs=["accountId", "currentBalance"],
        outputs=["summary"],
    )

    index = DesignObjectIndex(demo_root)
    index.build()
    convert_node(index, node, out_dir / "converted")

    print("\n--- Demo resolution report ---")
    print((out_dir / "converted" / f"{node.node_id}_resolution.json").read_text())
    java_file = next((out_dir / "converted").glob("*.java.txt"))
    print("\n--- Demo Java scaffold ---")
    print(java_file.read_text())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-root", type=Path, help="Path to local Appian design object export directory")
    parser.add_argument("--node-report", type=Path, help="Path to a process model's node report JSON")
    parser.add_argument("--node-id", type=str, help="ID of the script task node to convert")
    parser.add_argument("--out-dir", type=Path, default=Path("./converted"), help="Output directory")
    parser.add_argument("--inspect", type=Path, help="Dump parsed fields for one exported XML file, to calibrate parsing")
    parser.add_argument("--demo", action="store_true", help="Run a self-contained demo with synthetic data")
    args = parser.parse_args()

    if args.demo:
        run_demo(args.out_dir)
        return

    if args.inspect:
        inspect_file(args.inspect)
        return

    if not (args.export_root and args.node_report and args.node_id):
        parser.error("--export-root, --node-report and --node-id are all required "
                      "(or use --demo / --inspect)")

    index = DesignObjectIndex(args.export_root)
    index.build()
    node = load_node_from_report(args.node_report, args.node_id)
    convert_node(index, node, args.out_dir)


if __name__ == "__main__":
    main()
