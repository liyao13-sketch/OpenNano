# OpenNano

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
