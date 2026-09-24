---
name: appian-react-site-generator
description: Converts an Appian Site design object and its linked Interfaces into a React (MUI) frontend, drilling down through rule!/cons! references and extracting backend touchpoints (record queries, process starts, Integration-object calls) into endpoints.json. Use when asked to generate, replicate, port, or scaffold an Appian site/interface/page as React, or to identify which REST endpoints a migrated Java backend needs to support a given Appian site.
---

# Appian Site → React (MUI) Generator

## When to use this skill

Trigger this skill when the user:
- Asks to convert, port, replicate, scaffold, or "turn into React" an Appian
  **Site**, **page**, or **Interface**.
- Asks what backend endpoints a given Appian site/page depends on (this
  skill's `endpoints.json` output answers that directly).
- Is working on the Appian → Java + React migration and has reached the
  frontend-generation step (as opposed to the process-model-to-Java-backend
  step, which is a **different** skill/script family — see
  `appian_common.py`'s sibling node-converter suite for that side).
- Provides (or has previously exported) local Appian design-object XML and
  wants working `.jsx` output rather than a manual rewrite.

Do **not** use this skill for:
- Converting Appian process models / process nodes to Java — that's the
  separate node-converter script suite built on the same `appian_common.py`
  engine, for the backend side of the migration.
- Producing a pixel-perfect visual clone. This generator produces a
  functional, MUI-wired scaffold with clearly marked TODOs — see
  `DEVELOPER_GUIDE.md`'s Known Limitations section before promising
  visual fidelity to anyone.

## What it needs from the user

1. A local export of the Appian design objects involved: the target **Site**,
   every **Interface** it links to (directly or via nested `rule!`
   references), and every **Expression Rule** / **Constant** / **Integration**
   those interfaces depend on.
2. Either:
   - A pre-built JSON site report (`{"name": ..., "pages": [{"name", "urlStub",
     "interfaceRef"}, ...]}`), or
   - The raw exported Site XML (the script has a permissive fallback parser
     for this, `parse_site_xml`).

If the user hasn't exported this yet, tell them what's needed rather than
guessing at paths.

## How to run it

```bash
# Always run this first when picking up a NEW Appian export you haven't
# calibrated against yet -- confirms the XML parsing patterns actually match.
python scripts/appian_site_react_generator.py --inspect /path/to/some_interface.xml

# Prove the pipeline end-to-end with synthetic data (no real files needed)
python scripts/appian_site_react_generator.py --demo --out-dir ./demo_out

# Real run
python scripts/appian_site_react_generator.py \
    --export-root /path/to/local/appian_export \
    --site-report /path/to/site_report.json \
    --out-dir ./react_output
```

`scripts/appian_common.py` must sit alongside `appian_site_react_generator.py`
— it provides the shared design-object indexing and rule!/cons! drill-down
resolver (the same engine the Java node-converter scripts use).

## What comes out

- One `.jsx` file per page, plus one more per nested Interface that itself
  returns UI (reusable Appian interfaces become reusable React components).
- `App.jsx` — `react-router-dom` routes for every page.
- `endpoints.json` — every backend touchpoint the site depends on, tagged by
  component. This is the artifact to hand to whoever is building the Java
  REST API — it is a worklist of endpoints to expose, not scraped URLs (see
  `DEVELOPER_GUIDE.md` Section 2 for why Appian interfaces don't call URLs
  directly).
- `resolution_notes.json` — unresolved references and parser warnings.

## Read before promising anything about the output

`DEVELOPER_GUIDE.md` (same directory as this skill) has a full, numbered
**Known Limitations** section — the most important one being that rule-input
parameter binding is not implemented (an inlined rule's `ri!x` becomes
`props.x`, not necessarily the value that was actually passed at the call
site). Do not tell the user generated output is correct or complete without
having read that section; summarize the relevant limitations when handing
off output, the same way the node-converter suite flags confidence levels
per node type.

## Relationship to the rest of the migration toolkit

This skill and the process-model-to-Java skill share `appian_common.py` on
purpose — same design-object indexing, same drill-down resolver, same
honesty conventions (permissive XML parsing flagged for calibration via
`--inspect`, TODOs left explicit rather than guessed at, confidence levels
stated rather than implied). If you're extending one, check whether the fix
belongs in the shared engine so it benefits both.
