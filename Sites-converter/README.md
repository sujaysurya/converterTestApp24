# Appian Site → React (MUI) — Skill Package

- `SKILL.md` — when/how a Claude skill should use this converter.
- `agent.md` — a subagent definition that reads SKILL.md and runs the scripts
  (Claude Code-style: YAML frontmatter + system prompt). Adapt the frontmatter
  if you're using a different agent framework -- the workflow body applies regardless.
- `DEVELOPER_GUIDE.md` — exhaustive explanation of how the converter works,
  every mapping table, and a fully numbered Known Limitations section. Read
  this before trusting or shipping any generated output.
- `scripts/appian_site_react_generator.py` — the converter itself. Depends on:
- `scripts/appian_common.py` — shared design-object indexing + rule!/cons!
  drill-down resolver (same engine used by the companion Java node-converter
  suite for the process-model side of this migration).

Quick start:
```bash
cd scripts
python appian_site_react_generator.py --demo --out-dir ./demo_out
```
