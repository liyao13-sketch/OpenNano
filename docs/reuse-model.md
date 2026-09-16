# Reuse model — what is actually reusable, and how the pieces relate

> Status: living document (first cut 2026-09-15). It answers a question the codebase never answered
> explicitly: **which things are reusable, and what are the rules between them?**
> Companion: [`design.md`](design.md) (UI language), [`README.md`](../README.md) (scope and the three layers).

## The four planes

```
(1) vocabulary   code, read-only, pinned by cross-checks   "what kinds of things exist"
(2) assets       editable store, NEEDS version + author    "what a kind of thing looks like"
(3) instances    plan (project) · record (core CSV) · transfer (package)   "this particular one"
(3') segments    a named fragment of (3) — planned         "structure ± parameters, never results"
(4) derived      computed at read time, NOT a source       "views over (3)"
```

Why (4) is called out: several convenient numbers in this system — per-machine defaults, tuning-line
views, canvas layout, `run{N}` ordinals — are **computed from records**. They are views, not data.
Nothing in (2) or (3) may store them, and no feature may read them as if they were the record.

## Three constraints

**C1 · Reference and snapshot must be distinguishable.**
A plan currently mixes both: some fields *point at* an asset (an equipment or machine id) while others
*copy* the asset's content (parameter definitions, formulas, metadata). Nothing records which is which.
Consequence: editing an asset silently changes what an old plan resolves to, while the plan itself
has no way to notice.

- a reference carries `{id, version}`
- a snapshot carries `{copied_from: {id, version}, at}`

**C2 · One source per fact.**
Parameter values live in (2) and (3) only. (4) is always a view. A planned segment **references**;
when a segment deliberately freezes values it is marked as a snapshot and must state its version
and warn when the store has moved on since.

★ **Results are never reusable.** Two runs may share identical structure *and* identical parameters and
still produce different measurements — that is the normal case in an experiment, not a duplicate to be
merged. Therefore a reusable fragment holds **no** measurement, observation, key-value, run-state or
record-identifier fields; and the system must not de-duplicate, merge or warn about such look-alike runs.

**C3 · Composition is traceable.**
A plan records what it was composed from — segment (version), template (version), machine — so that
"why does this value differ from last month's?" has an answer without archaeology.

## What is deliberately *not* unified

- **Two key vocabularies coexist** (an asset-side set of human-facing keys, and a record-side set of
  unit-bearing contract keys). Merging them would touch stored assets, canvas labels and historical
  packages; the cheaper correct move is **one explicit mapping table plus a judgement that no asset key
  can be exported unmapped**. Until that judgement exists, treat an unmapped key as a defect, not a default.
- **Asset-side wording stays human**; code identifiers are not renamed for consistency's sake.

## Where the gaps are

| gap | symptom | planned remedy |
|---|---|---|
| references without versions | an old plan silently resolves to edited assets | C1 + a ratchet judgement freezing today's unversioned references |
| assets without version/author | "who changed this template, and to what?" is unanswerable | add version/updated_by/updated_at, and record the changed keys in the activity log |
| no plan-level provenance | differences between two plans of the same batch cannot be explained | C3 field on the project, round-tripped through export/import |

## Sequencing

1. this document (no code)
2. naming table (documents and UI wording only)
3. asset version + change log (small)
4. plan-level provenance (medium) — lands together with the first feature that consumes it

Each step above ships with a machine-checkable judgement; a constraint with no judgement is a wish.
