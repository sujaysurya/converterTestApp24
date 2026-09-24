#!/usr/bin/env python3
"""
appian_common.py
=================
Shared engine used by every per-node-type converter script in this suite
(appian_*_converter.py). This is the ONLY place the drill-down logic lives --
each node-type script imports this module and supplies only:
  (a) the node type's expected parameter schema, and
  (b) which Java scaffold "category" template applies to it.

WHY A SHARED MODULE INSTEAD OF 55 COPIES
------------------------------------------
The indexing / reference-extraction / recursive-resolution logic is IDENTICAL
regardless of node type -- a Write-to-Data-Store node's "valuesToStore"
parameter and a Send-E-Mail node's "subject" parameter are both just SAIL
expressions that may reference rule!/cons!/other design objects and need the
same drill-down treatment. Duplicating that logic 55 times would mean 55
places to fix the same bug. Each node-type script stays a thin, independently
runnable, independently downloadable file -- it just doesn't reinvent the
resolver.

ADAPT THE PARSING LAYER TO YOUR EXPORT
-----------------------------------------
Same caveat as before: `DesignObjectIndex._parse_object_file()` uses
permissive regex extraction because I don't have verified knowledge of your
export's exact XML schema. Run any node script with `--inspect <xml file>`
to check what it detects, and adjust the patterns in THIS file (it's shared,
so one fix applies to all 55 scripts).

PER-NODE-TYPE PARAMETER SCHEMAS: CONFIDENCE LEVELS
------------------------------------------------------
Each node script declares a PARAMETER_SCHEMA dict of {param_name: comment}.
Where the node type is a standard Appian process node or a well-documented
AppMarket plugin, the parameter names are best-effort accurate. Where the
node type is clearly a custom/internal plugin (version-suffixed names,
org-specific naming), the schema is a labeled placeholder -- verify against
`--inspect` output on a real exported node before trusting it.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Callable


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DesignObject:
    uuid: str
    name: str
    obj_type: str                  # 'expression_rule' | 'constant' | 'decision' | 'unknown'
    raw_expression: Optional[str]
    value: Optional[str]
    source_file: str


@dataclass
class NodeRecord:
    node_id: str
    node_name: str
    node_type: str                     # human label, e.g. "Write To Data Store Entity"
    parameters: dict                   # param_name -> raw SAIL expression / literal string
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)


@dataclass
class ResolvedParameter:
    name: str
    raw_expression: str
    resolved_expression: str
    referenced_design_objects: list = field(default_factory=list)
    unresolved_refs: list = field(default_factory=list)
    dynamic_refs: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class ResolvedNode:
    node_id: str
    node_name: str
    node_type: str
    parameters: dict                   # param_name -> ResolvedParameter (as dict after asdict())
    max_depth_reached: int = 0


# ---------------------------------------------------------------------------
# 1. Design object indexing  (ADAPT to your real export layout -- see above)
# ---------------------------------------------------------------------------

class DesignObjectIndex:
    NAME_PATTERN = re.compile(r"<name[^>]*>([^<]+)</name>", re.IGNORECASE)
    UUID_PATTERN = re.compile(r'uuid=["\']([a-f0-9\-]{20,})["\']', re.IGNORECASE)
    TYPE_HINTS = {
        "expression_rule": [r"expressionRule", r"<sail", r"ExpressionRule"],
        "decision": [r"DecisionFunction", r"decisionTable"],
        "constant": [r"<constant", r"ConstantDesignObject"],
    }
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
        value = raw_expression if (obj_type == "constant" and raw_expression) else None
        return DesignObject(uuid=uuid, name=name, obj_type=obj_type,
                             raw_expression=raw_expression, value=value, source_file=str(path))

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
DYNAMIC_RULE_PATTERN = re.compile(r"rule!\s*\(")


def extract_refs(expr: str) -> dict:
    refs = {"rule": set(), "cons": set(), "ri": set(), "pv": set(), "local": set(), "fv": set()}
    for prefix, name in REF_PATTERN.findall(expr):
        refs[prefix].add(name)
    return refs


# ---------------------------------------------------------------------------
# 3. Recursive drill-down resolver (shared across every parameter of a node,
#    and reusable across many nodes in one run for cross-node memoization)
# ---------------------------------------------------------------------------

class DrillDownResolver:
    def __init__(self, index: DesignObjectIndex, max_depth: int = 15):
        self.index = index
        self.max_depth = max_depth
        self._resolved_cache: dict[str, str] = {}
        self._visiting: set[str] = set()

    def resolve_node(self, node: NodeRecord) -> ResolvedNode:
        resolved_params = {}
        max_depth_reached = 0
        for pname, pexpr in node.parameters.items():
            rp = ResolvedParameter(name=pname, raw_expression=pexpr, resolved_expression=pexpr)
            self._visiting.clear()
            resolved_expr, depth = self._resolve_expression(pexpr, 0, rp)
            rp.resolved_expression = resolved_expr
            max_depth_reached = max(max_depth_reached, depth)
            resolved_params[pname] = asdict(rp)
        return ResolvedNode(node_id=node.node_id, node_name=node.node_name,
                             node_type=node.node_type, parameters=resolved_params,
                             max_depth_reached=max_depth_reached)

    def _resolve_expression(self, expr: str, depth: int, rp: ResolvedParameter):
        if not expr:
            return expr, depth
        if depth > self.max_depth:
            rp.warnings.append(f"max_depth ({self.max_depth}) exceeded -- stopped drilling further")
            return expr, depth
        if DYNAMIC_RULE_PATTERN.search(expr):
            rp.dynamic_refs.append("rule!(...) built dynamically -- cannot statically resolve, flag for manual review")

        refs = extract_refs(expr)
        max_child_depth = depth
        expr_out = expr

        for name in sorted(refs["rule"] | refs["cons"]):
            key_prefix = "rule" if name in refs["rule"] else "cons"
            cache_key = f"{key_prefix}!{name}"

            if cache_key in self._visiting:
                rp.warnings.append(f"circular reference detected at {cache_key} -- left as call, not inlined")
                continue

            if cache_key in self._resolved_cache:
                inlined = self._resolved_cache[cache_key]
            else:
                obj = self.index.find_by_name(name)
                if obj is None:
                    rp.unresolved_refs.append(cache_key)
                    continue
                rp.referenced_design_objects.append(f"{obj.obj_type}:{obj.name}")
                if obj.obj_type == "constant":
                    inlined = obj.value if obj.value is not None else f"/* unresolved constant value: {name} */"
                    child_depth = depth + 1
                else:
                    body = obj.raw_expression or f"/* no expression body found for {name} */"
                    self._visiting.add(cache_key)
                    inlined, child_depth = self._resolve_expression(body, depth + 1, rp)
                    self._visiting.discard(cache_key)
                self._resolved_cache[cache_key] = inlined
                max_child_depth = max(max_child_depth, child_depth)

            call_pattern = re.compile(rf"\b{key_prefix}!{re.escape(name)}\b(\([^)]*\))?")
            expr_out = call_pattern.sub(f"/* inlined {key_prefix}!{name} */ ({inlined})", expr_out)

        return expr_out, max_child_depth


# ---------------------------------------------------------------------------
# 4. Java naming / small primitive translation helpers
# ---------------------------------------------------------------------------

def java_identifier(name: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")
    if not ident:
        return "unnamed"
    if ident[0].isdigit():
        ident = "_" + ident
    return ident.lower()


def java_class_name(name: str) -> str:
    parts = re.sub(r"[^A-Za-z0-9]", " ", name).split()
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Unnamed"


_SIMPLE_TRANSLATIONS = [
    (re.compile(r"a!ifThenElse\s*\(\s*condition:\s*(.*?),\s*thenValue:\s*(.*?),\s*elseValue:\s*(.*?)\)", re.DOTALL),
     r"(\1) ? (\2) : (\3)"),
    (re.compile(r"\bconcat\s*\("), "String.join(\"\", "),
    (re.compile(r"\band\("), "allMatch("),
    (re.compile(r"\bor\("), "anyMatch("),
]


def best_effort_translate(expr: str) -> str:
    out = expr
    for pattern, replacement in _SIMPLE_TRANSLATIONS:
        out = pattern.sub(replacement, out)
    return out


def resolved_comment_block(resolved: ResolvedNode) -> list:
    """Common warning/provenance header used at the top of every generated method."""
    lines = []
    all_refs, all_unresolved, all_dynamic = [], [], []
    for p in resolved.parameters.values():
        all_refs += p["referenced_design_objects"]
        all_unresolved += p["unresolved_refs"]
        all_dynamic += p["dynamic_refs"]
    lines.append(f"// Generated scaffold for node: {resolved.node_id} ({resolved.node_name}) [{resolved.node_type}]")
    lines.append(f"// Drilled into {len(set(all_refs))} design object(s): {', '.join(sorted(set(all_refs))) or 'none'}")
    if all_unresolved:
        lines.append(f"// WARNING: unresolved references (not found in local export): {', '.join(sorted(set(all_unresolved)))}")
    if all_dynamic:
        lines.append(f"// WARNING: dynamic references requiring manual review: {'; '.join(all_dynamic)}")
    return lines


def resolved_param_lines(resolved: ResolvedNode, prefix: str = "    // ") -> list:
    lines = []
    for pname, p in resolved.parameters.items():
        translated = best_effort_translate(p["resolved_expression"])
        lines.append(f"{prefix}[{pname}] = {translated}".rstrip())
    return lines


# ---------------------------------------------------------------------------
# 5. Category-based Java scaffold templates
#    Each function: (resolved, node, extra) -> full Java method text
# ---------------------------------------------------------------------------

def _method_header(resolved, node, return_type="Object"):
    method_name = java_identifier(resolved.node_name or resolved.node_id)
    params = ", ".join(f"Object {java_identifier(i)}" for i in node.inputs) or ""
    return method_name, params, return_type


def tmpl_gateway(resolved, node, extra):
    """XOR / AND / OR / Complex gateways."""
    mode = extra.get("mode", "xor")
    method_name, params, _ = _method_header(resolved, node, "String")
    lines = resolved_comment_block(resolved)
    lines.append(f"// gateway type: {mode.upper()}")
    lines.append(f"public String {method_name}({params}) {{")
    if mode == "xor":
        lines.append("    // Exclusive: first matching branch wins.")
        for pname, p in resolved.parameters.items():
            cond = best_effort_translate(p["resolved_expression"])
            lines.append(f"    if ({cond}) {{ return \"{pname}\"; }}  // {pname}")
        lines.append("    throw new IllegalStateException(\"No XOR branch condition matched -- verify default flow\");")
    elif mode == "and":
        lines.append("    // Parallel fork: kick off every branch concurrently, join before continuing.")
        futures = []
        for pname in resolved.parameters:
            var = java_identifier(pname)
            lines.append(f"    java.util.concurrent.CompletableFuture<Void> {var} = "
                          f"java.util.concurrent.CompletableFuture.runAsync(() -> executeBranch(\"{pname}\"));")
            futures.append(var)
        lines.append(f"    java.util.concurrent.CompletableFuture.allOf({', '.join(futures)}).join();")
        lines.append("    return \"joined\";")
    elif mode == "or":
        lines.append("    // Inclusive: every branch whose condition is true is taken.")
        lines.append("    java.util.List<String> takenBranches = new java.util.ArrayList<>();")
        for pname, p in resolved.parameters.items():
            cond = best_effort_translate(p["resolved_expression"])
            lines.append(f"    if ({cond}) {{ takenBranches.add(\"{pname}\"); }}")
        lines.append("    return String.join(\",\", takenBranches);")
    else:  # complex
        lines.append("    // Complex gateway: evaluate branch conditions plus a custom join condition.")
        lines.append("    java.util.List<String> takenBranches = new java.util.ArrayList<>();")
        for pname, p in resolved.parameters.items():
            if pname == "joinCondition":
                continue
            cond = best_effort_translate(p["resolved_expression"])
            lines.append(f"    if ({cond}) {{ takenBranches.add(\"{pname}\"); }}")
        if "joinCondition" in resolved.parameters:
            jc = best_effort_translate(resolved.parameters["joinCondition"]["resolved_expression"])
            lines.append(f"    // join condition: {jc}")
            lines.append(f"    boolean joinSatisfied = ({jc});")
            lines.append("    if (!joinSatisfied) { throw new IllegalStateException(\"Complex gateway join condition not satisfied\"); }")
        lines.append("    return String.join(\",\", takenBranches);")
    lines.append("}")
    return "\n".join(lines)


def tmpl_subprocess(resolved, node, extra):
    mode = extra.get("mode", "call_wait")  # call_wait | start_async | start_async_deprecated
    method_name, params, _ = _method_header(resolved, node, "Object")
    lines = resolved_comment_block(resolved)
    if mode == "start_async_deprecated":
        lines.append("// DEPRECATED Appian node -- confirm whether the target process model is still active before migrating.")
    lines.append(f"public Object {method_name}({params}) {{")
    lines.append("    java.util.Map<String, Object> processParameters = new java.util.HashMap<>();")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    processParameters.put(\"{pname}\", {expr});  // was: {p['raw_expression'][:80]}")
    if mode == "call_wait":
        lines.append(f"    // Sub-process call: this delegates to the sub-process's OWN generated class/method")
        lines.append(f"    // (its internal nodes are converted the same way -- expressions/rules/constants get")
        lines.append(f"    // inlined THERE, they are not re-inlined here; this is a structural boundary, not a logic one).")
        lines.append(f"    return subProcessOrchestrationService.callAndWait(\"{resolved.node_name}\", processParameters);")
    else:
        lines.append(f"    // Fire-and-forget / independent process start -- does not block this flow.")
        lines.append(f"    return subProcessOrchestrationService.startAsync(\"{resolved.node_name}\", processParameters);")
    lines.append("}")
    return "\n".join(lines)


def tmpl_data_store_write(resolved, node, extra):
    multiplicity = extra.get("multiplicity", "single")  # single | multiple | records
    method_name, params, _ = _method_header(resolved, node, "void")
    lines = resolved_comment_block(resolved)
    lines.append(f"public void {method_name}({params}) {{")
    if multiplicity == "multiple":
        lines.append("    java.util.List<Object> entitiesToSave = new java.util.ArrayList<>();")
        for pname, p in resolved.parameters.items():
            expr = best_effort_translate(p["resolved_expression"])
            lines.append(f"    entitiesToSave.add(buildEntity(\"{pname}\", {expr}));  // TODO: map to real @Entity type(s)")
        lines.append("    entitiesToSave.forEach(this::saveViaAppropriateRepository);")
    else:
        lines.append("    Object entity = new Object();  // TODO: replace with the real @Entity / Record DTO type")
        for pname, p in resolved.parameters.items():
            expr = best_effort_translate(p["resolved_expression"])
            lines.append(f"    // set {pname}: {expr}")
        lines.append("    repository.save(entity);  // TODO: inject the correct Spring Data JPA repository")
    lines.append("}")
    return "\n".join(lines)


def tmpl_data_store_delete(resolved, node, extra):
    method_name, params, _ = _method_header(resolved, node, "void")
    lines = resolved_comment_block(resolved)
    lines.append(f"public void {method_name}({params}) {{")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    // {pname}: {expr}")
    lines.append("    java.util.List<Object> idsOrKeys = resolveKeysToDelete();  // TODO: derive from parameters above")
    lines.append("    repository.deleteAllByIdInBatch(idsOrKeys);  // TODO: inject the correct repository")
    lines.append("}")
    return "\n".join(lines)


def tmpl_start_node(resolved, node, extra):
    method_name = java_identifier(resolved.node_name or resolved.node_id)
    lines = resolved_comment_block(resolved)
    lines.append(f"public ProcessContext {method_name}Start(")
    args = ", ".join(f"Object {java_identifier(i)}" for i in node.inputs)
    lines[-1] += f"{args}) {{"
    lines.append("    ProcessContext context = new ProcessContext();  // TODO: real process-variable-backed context/DTO")
    for i in node.inputs:
        lines.append(f"    context.set(\"{i}\", {java_identifier(i)});")
    lines.append("    return context;")
    lines.append("}")
    return "\n".join(lines)


def tmpl_end_node(resolved, node, extra):
    method_name = java_identifier(resolved.node_name or resolved.node_id)
    lines = resolved_comment_block(resolved)
    lines.append(f"public Object {method_name}End(ProcessContext context) {{")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    // output [{pname}]: {expr}")
    lines.append("    return context.buildResponseDto();  // TODO: shape the real return DTO from process variables")
    lines.append("}")
    return "\n".join(lines)


def tmpl_user_task(resolved, node, extra):
    method_name, params, _ = _method_header(resolved, node, "TaskHandle")
    lines = resolved_comment_block(resolved)
    lines.append("// STRUCTURAL NODE: human-in-the-loop. This is not a pure function -- it creates a")
    lines.append("// task and suspends the flow until a person acts. Needs your target workflow/task-queue")
    lines.append("// decision (this was flagged in the top-level plan as its own architectural question).")
    lines.append(f"public TaskHandle {method_name}({params}) {{")
    lines.append("    Map<String, Object> taskData = new HashMap<>();")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    taskData.put(\"{pname}\", {expr});")
    lines.append(f"    return taskService.createTask(\"{resolved.node_name}\", taskData);  // TODO: wire real task queue")
    lines.append("}")
    return "\n".join(lines)


def tmpl_stored_proc(resolved, node, extra):
    method_name, params, _ = _method_header(resolved, node, "Object")
    lines = resolved_comment_block(resolved)
    lines.append(f"public Object {method_name}({params}) {{")
    lines.append("    java.util.Map<String, Object> callParams = new java.util.HashMap<>();")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    callParams.put(\"{pname}\", {expr});")
    lines.append(f"    // TODO: replace with a real SimpleJdbcCall / CallableStatement against \"{resolved.node_name}\"")
    lines.append("    return jdbcTemplate.call(conn -> conn.prepareCall(\"{call PROC_NAME(?, ?)}\"), callParams);")
    lines.append("}")
    return "\n".join(lines)


def tmpl_integration_call(resolved, node, extra):
    method_name, params, _ = _method_header(resolved, node, "ResponseEntity<String>")
    lines = resolved_comment_block(resolved)
    lines.append(f"public ResponseEntity<String> {method_name}({params}) {{")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    // {pname}: {expr}")
    lines.append("    // TODO: replace with the real endpoint/method/headers from this integration's config,")
    lines.append("    // and prefer a typed WebClient/Feign client over raw RestTemplate for a banking integration.")
    lines.append("    HttpHeaders headers = new HttpHeaders();")
    lines.append("    HttpEntity<String> request = new HttpEntity<>(/* body */ null, headers);")
    lines.append(f"    return restTemplate.exchange(/* url */ \"TODO\", HttpMethod.POST, request, String.class);")
    lines.append("}")
    return "\n".join(lines)


def tmpl_email(resolved, node, extra):
    method_name, params, _ = _method_header(resolved, node, "void")
    lines = resolved_comment_block(resolved)
    lines.append(f"public void {method_name}({params}) throws MessagingException {{")
    lines.append("    MimeMessage message = mailSender.createMimeMessage();")
    lines.append("    MimeMessageHelper helper = new MimeMessageHelper(message, true);")
    known = {"to": "setTo", "cc": "setCc", "bcc": "setBcc", "subject": "setSubject", "body": "setText"}
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        if pname in known:
            lines.append(f"    helper.{known[pname]}(String.valueOf({expr}));")
        else:
            lines.append(f"    // {pname}: {expr}  // TODO: map to the right MimeMessageHelper call")
    lines.append("    mailSender.send(message);")
    lines.append("}")
    return "\n".join(lines)


def tmpl_event_consume(resolved, node, extra):
    method_name, params, _ = _method_header(resolved, node, "Object")
    lines = resolved_comment_block(resolved)
    lines.append("// STRUCTURAL NODE: suspends flow until a matching event/signal arrives.")
    lines.append(f"public Object {method_name}({params}) {{")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    // {pname}: {expr}")
    lines.append("    return eventBus.waitFor(/* eventKey */ \"TODO\", /* timeout */ Duration.ofHours(24));")
    lines.append("}")
    return "\n".join(lines)


def tmpl_ai_text(resolved, node, extra):
    method_name, params, _ = _method_header(resolved, node, "String")
    lines = resolved_comment_block(resolved)
    lines.append(f"public String {method_name}({params}) {{")
    prompt_expr = None
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    // {pname}: {expr}")
        if "prompt" in pname.lower() or "template" in pname.lower():
            prompt_expr = expr
    lines.append(f"    String prompt = {prompt_expr or '/* TODO: identify the prompt/template parameter */ \"\"'};")
    lines.append("    return textGenerationService.generate(prompt);  // TODO: wire to your target generation service")
    lines.append("}")
    return "\n".join(lines)


def tmpl_record_sync(resolved, node, extra):
    method_name, params, _ = _method_header(resolved, node, "void")
    lines = resolved_comment_block(resolved)
    lines.append(f"public void {method_name}({params}) {{")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    // {pname}: {expr}")
    lines.append("    recordSyncService.sync(/* record type */ \"TODO\");  // TODO: wire to real sync mechanism")
    lines.append("}")
    return "\n".join(lines)


def tmpl_service_call(resolved, node, extra):
    """Generic single-call-out template, parameterized by service field name +
    operation, used for Document Management / Excel-CSV / Users&Groups /
    Alert-config style smart services."""
    service_field = extra.get("service_field", "smartService")
    operation = extra.get("operation", java_identifier(resolved.node_name))
    return_type = extra.get("return_type", "Object")
    method_name, params, _ = _method_header(resolved, node, return_type)
    lines = resolved_comment_block(resolved)
    lines.append(f"public {return_type} {method_name}({params}) {{")
    lines.append("    java.util.Map<String, Object> callParams = new java.util.HashMap<>();")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    callParams.put(\"{pname}\", {expr});")
    lines.append(f"    return {service_field}.{operation}(callParams);  // TODO: replace with the real service call")
    lines.append("}")
    return "\n".join(lines)


def tmpl_kafka_produce(resolved, node, extra):
    bulk = extra.get("bulk", False)
    method_name, params, _ = _method_header(resolved, node, "void")
    lines = resolved_comment_block(resolved)
    lines.append(f"public void {method_name}({params}) {{")
    topic_expr, payload_expr, key_expr = None, None, None
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"    // {pname}: {expr}")
        low = pname.lower()
        if "topic" in low:
            topic_expr = expr
        elif "payload" in low or "message" in low or "value" in low:
            payload_expr = expr
        elif "key" in low:
            key_expr = expr
    topic_expr = topic_expr or "/* TODO: identify topic parameter */ \"TODO_TOPIC\""
    payload_expr = payload_expr or "/* TODO: identify payload parameter */ null"
    if bulk:
        lines.append(f"    java.util.List<Object> payloads = {payload_expr} instanceof java.util.List "
                      f"? (java.util.List<Object>) {payload_expr} : java.util.List.of({payload_expr});")
        lines.append(f"    payloads.forEach(p -> kafkaTemplate.send(String.valueOf({topic_expr}), "
                      f"{key_expr or 'null'}, p));")
    else:
        lines.append(f"    kafkaTemplate.send(String.valueOf({topic_expr}), {key_expr or 'null'}, {payload_expr});")
    lines.append("}")
    return "\n".join(lines)


def tmpl_kafka_consume(resolved, node, extra):
    deprecated = extra.get("deprecated", False)
    method_name = java_identifier(resolved.node_name or resolved.node_id)
    lines = resolved_comment_block(resolved)
    if deprecated:
        lines.append("// DEPRECATED Appian node -- confirm whether this listener is still actively used.")
    for pname, p in resolved.parameters.items():
        expr = best_effort_translate(p["resolved_expression"])
        lines.append(f"// {pname}: {expr}")
    lines.append("@KafkaListener(topics = \"TODO_TOPIC\", groupId = \"TODO_GROUP\")")
    lines.append(f"public void {method_name}(ConsumerRecord<String, String> record) {{")
    lines.append("    // TODO: dispatch record.value() into the equivalent of this node's downstream logic")
    lines.append("}")
    return "\n".join(lines)


CATEGORY_TEMPLATES: dict[str, Callable] = {
    "GATEWAY": tmpl_gateway,
    "SUBPROCESS": tmpl_subprocess,
    "DATA_STORE_WRITE": tmpl_data_store_write,
    "DATA_STORE_DELETE": tmpl_data_store_delete,
    "START_NODE": tmpl_start_node,
    "END_NODE": tmpl_end_node,
    "USER_TASK": tmpl_user_task,
    "STORED_PROC": tmpl_stored_proc,
    "INTEGRATION_CALL": tmpl_integration_call,
    "EMAIL": tmpl_email,
    "EVENT_CONSUME": tmpl_event_consume,
    "AI_TEXT": tmpl_ai_text,
    "RECORD_SYNC": tmpl_record_sync,
    "SERVICE_CALL": tmpl_service_call,
    "KAFKA_PRODUCE": tmpl_kafka_produce,
    "KAFKA_CONSUME": tmpl_kafka_consume,
}


# ---------------------------------------------------------------------------
# 6. Orchestration shared by every node-type script's main()
# ---------------------------------------------------------------------------

def convert_node(index: DesignObjectIndex, node: NodeRecord, out_dir: Path,
                  category: str, extra: dict, resolver: Optional[DrillDownResolver] = None) -> ResolvedNode:
    resolver = resolver or DrillDownResolver(index)
    resolved = resolver.resolve_node(node)

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{node.node_id}_resolution.json"
    report_path.write_text(json.dumps(asdict(resolved), indent=2), encoding="utf-8")

    template_fn = CATEGORY_TEMPLATES[category]
    java_code = template_fn(resolved, node, extra)
    java_path = out_dir / f"{java_identifier(node.node_name or node.node_id)}.java.txt"
    java_path.write_text(java_code, encoding="utf-8")

    print(f"[convert] node {node.node_id} ({node.node_type}) -> {report_path.name}, {java_path.name}")
    any_unresolved = any(p["unresolved_refs"] for p in resolved.parameters.values())
    if any_unresolved:
        print(f"          UNRESOLVED refs present -- check {report_path.name}")
    return resolved


def load_node_from_report(node_report_path: Path, node_id: str) -> NodeRecord:
    """
    ADAPT to your actual per-process-model report JSON schema. Assumes:
    {"nodes": [{"id":..., "name":..., "type":..., "parameters": {...},
                "inputs":[...], "outputs":[...]}, ...]}
    """
    data = json.loads(node_report_path.read_text(encoding="utf-8"))
    for n in data.get("nodes", []):
        if n.get("id") == node_id:
            return NodeRecord(node_id=n["id"], node_name=n.get("name", n["id"]),
                               node_type=n.get("type", "Unknown"),
                               parameters=n.get("parameters", {}),
                               inputs=n.get("inputs", []), outputs=n.get("outputs", []))
    raise KeyError(f"node id {node_id} not found in {node_report_path}")


def inspect_file(path: Path) -> None:
    idx = DesignObjectIndex(path.parent)
    obj = idx._parse_object_file(path)
    if obj is None:
        print(f"Could not detect a <name> element in {path}. Adjust DesignObjectIndex "
              f"in appian_common.py (NAME_PATTERN / EXPR_BODY_PATTERNS) to match your export.")
        return
    print(json.dumps(asdict(obj), indent=2)[:3000])
    print("\nIf obj_type/raw_expression look wrong, adjust TYPE_HINTS / EXPR_BODY_PATTERNS "
          "in appian_common.py to match this file's real tags -- the fix applies to all 55 scripts.")


def build_arg_parser(node_type_label: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Convert Appian '{node_type_label}' nodes into Java method scaffolds, "
                    f"with full rule!/cons! drill-down resolution.")
    parser.add_argument("--export-root", type=Path, help="Path to local Appian design object export directory")
    parser.add_argument("--node-report", type=Path, help="Path to a process model's node report JSON")
    parser.add_argument("--node-id", type=str, help="ID of the node to convert")
    parser.add_argument("--out-dir", type=Path, default=Path("./converted"), help="Output directory")
    parser.add_argument("--inspect", type=Path, help="Dump parsed fields for one exported XML file")
    parser.add_argument("--demo", action="store_true", help="Run a self-contained demo with synthetic data")
    return parser
