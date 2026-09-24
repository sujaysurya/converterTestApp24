#!/usr/bin/env python3
"""
appian_reactivate_users_converter.py
====================================
Converts Appian 'Reactivate Users' nodes into Java method scaffolds, with full
rule!/cons! drill-down resolution via appian_common.py.

CONFIDENCE NOTE: Well-documented Users & Groups Management plugin -- accurate.

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

NODE_TYPE_LABEL = "Reactivate Users"
NODE_CATEGORY = "SERVICE_CALL"
EXTRA_CONFIG = {'service_field': 'identityManagementService', 'operation': 'reactivateUsers', 'return_type': 'boolean'}

PARAMETER_SCHEMA = {'users': 'users to reactivate'}


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
        node_id="demo-reactivate_users",
        node_name="Demo Reactivate Users",
        node_type=NODE_TYPE_LABEL,
        parameters={'users': 'pv!returningUsers'},
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
