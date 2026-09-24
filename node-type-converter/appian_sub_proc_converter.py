#!/usr/bin/env python3
"""
appian_sub_proc_converter.py
============================
Converts Appian 'Sub_PROC' nodes into Java method scaffolds, with full
rule!/cons! drill-down resolution via appian_common.py.

CONFIDENCE NOTE: Standard Appian process node -- accurate.

PARAMETER_SCHEMA below documents what this script expects to find in each
node's `parameters` dict (see appian_common.load_node_from_report). Adjust it
-- and the demo data further down -- once you've confirmed your export's real
field names via `--inspect`.
"""

from pathlib import Path

from appian_common import (
    DesignObjectIndex, DrillDownResolver, NodeRecord,
    convert_node, load_node_from_report, inspect_file, build_arg_parser,
)

NODE_TYPE_LABEL = "Sub_PROC"
NODE_CATEGORY = "SUBPROCESS"
EXTRA_CONFIG = {'mode': 'call_wait'}

PARAMETER_SCHEMA = {'subprocessModelName': 'Name/UUID of the target sub-process model (verify against export)', 'inputMapping': 'SAIL expression(s) mapping parent process variables to sub-process inputs'}


def _demo_export(demo_root: Path) -> None:
    """Shared demo design objects so every generated script proves drill-down
    (rule! -> rule! -> cons!) works end-to-end without needing your real export."""
    demo_root.mkdir(parents=True, exist_ok=True)
    (demo_root / "DemoThreshold.xml").write_text("""
    <constant uuid="demo-c-0001">
      <name>DemoThreshold</name>
      <value>500</value>
    </constant>
    """, encoding="utf-8")
    (demo_root / "demoBusinessRule.xml").write_text("""
    <expressionRule uuid="demo-r-0002">
      <name>demoBusinessRule</name>
      <definition>
        a!ifThenElse(
          condition: ri!balance &gt;= cons!DemoThreshold,
          thenValue: ri!balance,
          elseValue: cons!DemoThreshold
        )
      </definition>
    </expressionRule>
    """, encoding="utf-8")


def _demo_node() -> NodeRecord:
    return NodeRecord(
        node_id="demo-sub_proc",
        node_name="Demo Sub_PROC",
        node_type=NODE_TYPE_LABEL,
        parameters={'subprocessModelName': '"Loan Approval Subprocess"', 'accountId': 'pv!accountId', 'approvalThreshold': 'rule!demoBusinessRule(balance: pv!currentBalance)'},
        inputs=["accountId", "currentBalance"],
        outputs=[],
    )


def run_demo(out_dir: Path) -> None:
    demo_root = out_dir / "_demo_export"
    _demo_export(demo_root)
    index = DesignObjectIndex(demo_root)
    index.build()
    node = _demo_node()
    resolved = convert_node(index, node, out_dir / "converted", NODE_CATEGORY, EXTRA_CONFIG)
    print("\n--- Demo resolution: parameters resolved ---")
    for pname, p in resolved.parameters.items():
        print(f"  [{pname}] -> {p['resolved_expression'][:160]}")
    java_files = sorted((out_dir / "converted").glob("*.java.txt"))
    if java_files:
        print("\n--- Demo Java scaffold ---")
        print(java_files[-1].read_text())


def main() -> None:
    parser = build_arg_parser(NODE_TYPE_LABEL)
    args = parser.parse_args()

    if args.demo:
        run_demo(args.out_dir)
        return
    if args.inspect:
        inspect_file(args.inspect)
        return
    if not (args.export_root and args.node_report and args.node_id):
        parser.error("--export-root, --node-report and --node-id are all required (or use --demo / --inspect)")

    index = DesignObjectIndex(args.export_root)
    index.build()
    node = load_node_from_report(args.node_report, args.node_id)
    resolver = DrillDownResolver(index)
    convert_node(index, node, args.out_dir, NODE_CATEGORY, EXTRA_CONFIG, resolver=resolver)


if __name__ == "__main__":
    main()
