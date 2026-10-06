<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/public/brand/logo-lockup-dark.svg">
    <img src="web/public/brand/logo-lockup-light.svg" alt="OpenNano" height="40">
  </picture>
</h1>

> **An organizational-memory system for micro/nano fabrication** — process flow canvas + LLM agent + reliability-scored knowledge base + data-driven process optimization.

Micro/nano fabrication knowledge lives in engineers' heads and on cleanroom paper. OpenNano turns it into **computable, searchable, inheritable** assets: a visual process-flow canvas that actually *runs*, a knowledge base where every entry carries a **source and a reliability score**, and an optimization engine (DOE → GPR surrogate → Bayesian optimization) that works on your own measured data.

## Why it is different

| Capability | OpenNano | Typical tools |
|---|---|---|
| Process flow as an **executable workflow** | ✅ run / run-to, stale propagation, disable steps | ❌ drawing only |
| Knowledge with **provenance + reliability** | ✅ source · verification state · 1–5 score | ❌ plain notes |
| **Parameter influence rules** (qualitative + quantitative) | ✅ `when` conditions + expressions + mechanism | ❌ fixed tables |
| **Optimization on real lab data** | ✅ DOE (full/partial/BBD/CCD) → GPR → BO (EI) | ❌ spreadsheets |
| **Experiment package ⇄ canvas** bridge | ✅ folder in/out, column-compatible with the data store | ❌ manual re-typing |
| **Fill measured data inside the tool** | ✅ form editor writes the pack's `measurements`/`observations` **in place** — no "export → hand-edit CSV → re-import" round trip | ❌ manual CSV editing |
| LLM agent that **queries data and drives the canvas** | ✅ 16 tools, auditable traces | ❌ chat-only |

*(screenshot placeholder — `docs/screenshot-canvas.png`)*

> Repo: https://github.com/liyao13-sketch/OPENNANO · License: Apache-2.0

## Quick start

```bash
git clone https://github.com/liyao13-sketch/OPENNANO.git
cd OPENNANO

# backend
cd server
python3 -m venv .venv && . .venv/bin/activate      # or: uv venv .venv
pip install -r requirements.txt
cp .env.example .env        # put your LLM key here (DeepSeek / OpenAI-compatible)
uvicorn main:app --port 8000 --reload

# frontend (another terminal)
cd web && npm install && npm run dev
```

Open **http://localhost:5173**. Without an LLM key the agent runs in mock mode — everything else works.

## Fill a pack in the tool (no CSV editing)

The workflow used to be *export pack → edit `measurements.csv` by hand → import back*. Now the
bottom **Data fill** panel does that step in place:

1. **Data fill** (bottom dock) → pick a pack. The repo ships a synthetic one
   (`samples/expack/DEMO-T1`) — it is **read-only**; hit **Copy to workspace** to get a writable copy
   under `~/.opennano/packs/`.
2. Pick a run, then add **measurement** rows (`quantity` / `value` / `unit` / `method` / `loc` / `n` /
   `uncertainty` / `verification` / `note`) and **observation** rows (`obs_type` / `severity` /
   `description`). `run_id` and `quantity` are required; leave `meas_id`/`obs_id` blank and the tool
   assigns `{run_id}.Mnn` / `{run_id}.Onn` — it **tells you** what it filled in.
3. **Save to pack** writes the two CSVs atomically, keeps a backup, and reloads. Then
   **Download pack (.zip)** hands the filled pack to whoever lands it into the data store.

Guard rails (all server-side, reported to the UI):

| Guard | Behaviour |
|---|---|
| Only two tables | `measurements.csv` / `observations.csv`; columns are read from the pack itself, so it cannot add or drop a column. `runs` / `steps` / `batches` / `manifest` are never touched, and no run is ever created (unknown `run_id` ⇒ error listing the valid ones). |
| Measurement anchor (§15.1) | `measurement.run_id` is *the process run this value was measured after* — a **metrology run must not carry measurements**; that is rejected with a pointer to its parent run. |
| No inference | Blank values are dropped (blank ≠ 0). Columns you don't submit keep their on-disk value; an explicitly empty string clears them. |
| Concurrency | Saving requires the `sha256` revisions from load; if the file changed underneath you get **409** and are told to reload (no silent overwrite). |
| No mass delete | If the on-disk table is non-empty and the submit would write **0 rows**, it refuses unless you pass `allow_clear`. Every rewritten table is backed up under `~/.opennano/packs/_backups/<batch>/`. |
| Write scope | Only packs under the workspace root (`~/.opennano/packs/`, or `OPENNANO_PACK_ROOTS`) are writable; repo samples and arbitrary paths are not. |

Endpoints: `GET /api/pack/list` · `POST /api/pack/{load,save,copy}` · `GET /api/pack/download`.
Vocabulary (quantity names, `obs_type`, `method`, `verification`) is read from the data-store contract
when reachable and otherwise falls back to the pack plus `samples/core` — on a fresh clone the pickers
are still populated.

## Layout

```
server/            FastAPI backend
  engine/          process catalog · parameter templates · formula engine · DOE
  opt/             GPR surrogate · Bayesian optimization (EI) · response-surface plots
  kb/              knowledge base · influence rules · core adapter · experiment packages
  tests/           regression net (pytest) — see "Tests" below
  agent/           LLM client · tool registry (16 tools) · RAG · orchestrator
web/               React + TypeScript + React Flow canvas
samples/           synthetic demo data (no real lab data)
docs/              design docs (architecture, optimization roadmap, package spec)
```

## Repository scope

This repository is the **tool layer**: how to *do* things (rules, judgments, mechanisms).
It deliberately does **not** carry the case layer — real lab records, instrument
inventories, vendor/model registries, or evidence pointers — which stay in the
operator's private workspace and are injected at runtime as configuration.

Three layers, kept apart on purpose:

| Layer | Who sees it | May contain identifying detail |
|---|---|---|
| cases / raw records | the operator only | yes — that is the point |
| rules / knowledge | the tool + the team (intranet) | yes — a rule is only accurate with it |
| **this public repo** | everyone | **no** |

⚠️ Consequence worth stating plainly: the instrument identities that remain in the
source (e.g. entries in `server/kb/core_vocab.py`) are **known, deliberate debt**,
scheduled to be externalised into a loadable inventory rather than deleted — a tool
that cannot tell *which* machine it is looking at gives the wrong parameters.
`server/tests/test_public_layer_hygiene.py` freezes that debt so it cannot grow,
and `LICENSE` carries a neutral holder.

## Tests

The regression net pins the rules that are expensive to re-derive: run numbering and
branch-safe continuation (`AR50-T1`-style forks), sample inheritance, usage tiers
(`split` vs `allocate`), append-package provenance, batch-event proposals, the
cross-line pointer check, and menu `group N = [2(chuck), N(etch), 4(dechuck)]` readout.

```bash
cd server
python -m venv .venv-test && . .venv-test/bin/activate    # or: uv venv .venv-test
pip install -r requirements-dev.txt
python -m pytest tests -rs        # -rs prints why anything was skipped
python -m kb.pointer_check --strict   # cross-line pointers (uses --allow-missing in CI)
```

Discipline the suite enforces by machine rather than memory:

- tests are **offline** and **never write lab data** — synthetic fixtures live in `tmp_path`;
- cases that need real sources (the `core` CSVs, equipment-menu dumps, the schema) **skip with a
  reason** on machines that do not have them, instead of passing vacuously;
- `kb/*.py` must not perform write operations against `core/` or `ingest/`.

## Core concepts

- **Organization memory** — every knowledge entry: `process_type · material · parameters · results · source · reliability_score`.
- **Influence rules** — `from → to` plus an optional `when` condition and a quantitative expression; both qualitative mechanism and numbers live in one object.
- **Machines vs process templates** — a process template owns parameter interfaces; a machine is a physical instance (`tool_id`) so machine-to-machine drift is modelled, not averaged away.
- **Experiment package** — a folder that is column-compatible with the lab data store, so collection → database → optimization is one continuous chain.

## Status

v1 focuses on: canvas + optimization + organizational memory. Literature management, equipment PM and spare-part tracking are on the roadmap.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
