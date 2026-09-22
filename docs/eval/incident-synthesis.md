# Incident-to-eval synthesis

Bernstein already captures incidents in four places: the dead-letter
queue, orchestrator postmortems, the flaky-test detector, and
CI-failure postmortems mined from merged pull requests. Until this
feature, those records stayed where they were created and never
became a **gate** on future runs. The same prompt-injection /
token-runaway / adapter-timeout patterns surfaced as repeat incidents
weeks apart, and CI regressions that the human author had to fix up
across 2+ commits never made it into the eval corpus at all.

`incident_synthesizer` ingests each incident, redacts secrets,
extracts the smallest reproducible trigger, and emits a YAML eval
case. The eval corpus thus grows from production failures. The
`incident_evals` quality gate (see below) then checks that every P0
case has recorded proof of a regression run before allowing merge -
it does not itself execute the cases against an agent.

## Why it exists

"Log and forget" was the failure mode. The fix is "every P0/P1
incident adds one regression case that future agents must pass."
That closes the loop.

## How to use it

Run on demand or on the `task_terminally_failed` lifecycle hook:

```bash
# Sync now: read every incident, emit YAML cases under
# src/bernstein/eval/cases/incidents/
bernstein eval sync-incidents

# Dry-run to see what would be generated without writing
bernstein eval sync-incidents --dry-run
```

The synthesiser:

1. Reads dead-letter queue, orchestrator postmortem artefacts, and
   CI-failure postmortems under `.sdd/reports/ci_postmortems/`.
2. Strips secrets via `core/security/sanitize.py`.
3. Extracts the smallest trigger - the failing prompt, failing config,
   failing tool-call sequence.
4. Writes one YAML per incident, idempotent by content hash *and*
   `source_incident` key.

### CI-failure postmortems

The companion `scripts/scrape_ci_postmortems.py` walks merged pull
requests from the last 30 days via the `gh` CLI. A PR qualifies as a
post-mortem when its commit list shows a feature commit followed by
**two or more fix-up commits**. The fix-up regex (see
`FIXUP_SUBJECT_RE` in the script) matches conventional-commit
prefixes like `fix(ci):`, `fix(tests):`, `fix(lint):`, `fix(types):`,
`fixup!`, `squash!`, and the plain-prefix variants `fix ci:` /
`fix tests:` / `fix typing:`. The first commit of the PR is always
treated as the original feature commit and never counted as a fix-up.

Each qualifying PR becomes one JSON record under
`.sdd/reports/ci_postmortems/pr-<PR#>-<short-sha>.json`. The next
`bernstein eval sync-incidents` run promotes that record into a
P1 (warn-only) regression case keyed on
`ci-postmortem:<PR#>:<commit-sha>`. Re-running either the scraper or
the synthesizer is a no-op once the case exists on disk.

Run the scraper on a daily cron (or after every release):

```bash
python scripts/scrape_ci_postmortems.py \
    --repo sipyourdrink-ltd/bernstein \
    --since-days 30 \
    --out .sdd/reports/ci_postmortems
```

When the `gh` CLI is missing or unauthenticated the scraper logs a
notice and exits 0 - downstream integration tests skip rather than
fail.

Sample emitted case:

```yaml
id: inc-prompt-injection-2026-04-22
severity: P0
prompt: |
  <minimal trigger that surfaced the original failure>
expected_outcome:
  - "agent refuses to follow injected instruction"
  - "audit log carries DECISION_DENIED"
source_incident: postmortem-2026-04-22-T14:33:11Z
```

### The `incident_evals` quality gate

`incident_evals` is a **proof-checking** gate, not a re-run of the
incidents: it does not execute the synthesised cases against an agent. For
every P0 case's YAML file under `src/bernstein/eval/cases/incidents/`, it
checks whether a corresponding proof-of-regression-run marker exists at

```
<runner/project workdir>/.sdd/eval/incident_results/<case-id>.json
```

A missing marker for any P0 case fails the gate and blocks merge (the gate
only checks *presence* of the file - the file's own harness is responsible
for writing real evaluation output there, never an empty placeholder). P1
and P2 cases are counted in the gate's `metadata` (`{"P0": n, "P1": n,
"P2": n}`) but do not affect the pass/fail verdict; the current
implementation does not emit a separate warning status for them.

Two directories matter and they are **not** the same thing:

- The **incident corpus** (the case YAML files) is read from the agent's
  own worktree under review.
- The **proof marker** is read from the runner/project's trusted `.sdd`
  root, which is a separate, git-tracked-adjacent location the agent under
  review does not control. This distinction exists because the worktree's
  `.sdd` directory is gitignored and disposable - if proof were accepted
  from there, an agent could satisfy a P0 block by writing an empty marker
  file in its own worktree without ever producing real evidence.

There is no operator procedure to manually clear a P0 block by hand-writing
a marker file: the marker is expected to be the real output of whatever
harness ran the case, written into the trusted workdir. If a P0 case is
blocking and there is no harness wired up to produce that proof yet, the
gate is doing its job - the case has no verified fix.

`incident_evals` is **not** part of the default gate pipeline: unlike most
gates, `QualityGatesConfig` has no `incident_evals` boolean flag, so
`build_default_pipeline()` never includes it automatically. To run it,
declare an explicit pipeline that lists it:

```yaml
quality_gates:
  pipeline:
    - { name: "lint", required: true, condition: "always" }
    - { name: "incident_evals", required: true, condition: "always" }
```

See [Quality Pipeline](../architecture/quality-pipeline.md#gates) for the
full pipeline-configuration mechanism.

`incident_evals` results are never cached: its verdict depends on
filesystem state (the corpus and the proof markers) outside the
changed-file set the gate cache keys off of, so it always re-checks on
every run rather than risking a stale PASS.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `eval.incident_sync.on_terminal_failure` | `true` | Auto-sync on every dead-letter event (`task_lifecycle.py`). |
| `eval.incident_sync.write_path` | `src/bernstein/eval/cases/incidents/` | Where the YAML cases live. |

`eval.gate_severity_blocking` does not exist: nothing in the codebase reads
it. The gate hard-codes P0 as the only blocking severity; P1/P2 are always
non-blocking. Treat any reference to this knob elsewhere as stale.

Metrics:

- `bernstein_incident_evals_total{severity}`
- `bernstein_incident_recurrence_rate`

## Scope

- One incident produces one case (no LLM-driven fuzz expansion).
- The corpus is operator-pruned: old cases accumulate until manually
  trimmed.
- Each project keeps its own corpus; cross-project incident sharing is
  not part of this surface.
- The minimaliser extracts the trigger using deterministic rules; it
  does not understand semantic intent. For unusual incident shapes
  the case may need hand-editing.
- `incident_evals` treats a missing corpus directory (no
  `src/bernstein/eval/cases/incidents/`) as a clean pass, not
  `skipped`/`inconclusive` like some other gates (e.g.
  `migration_reversibility`). Left as-is for now - changing it is a
  behavior change beyond this gate's evidence-handling fix and would need
  its own justification.
- The severity parser (`_severity_from_yaml`) only recognises a plain
  `severity: P0`-style line; a quoted value (`severity: "P0"`) or a
  trailing comment is not recognised and the case is silently excluded
  from the P0/P1/P2 counts. This matches what `IncidentSynthesizer`
  itself always emits (plain, unquoted, comment-free), so it is not
  currently reachable through the normal synthesis path - only through a
  hand-edited case file.

## Related

- Source: `src/bernstein/eval/incident_synthesizer.py`
- Inputs: `core/tasks/dead_letter_queue.py`,
  `core/observability/postmortem.py`,
  `scripts/scrape_ci_postmortems.py`
- Quality gate: `core/quality/gate_pipeline.py`
- CLI: `bernstein eval sync-incidents`
- PR #1001 (initial), #1793 (CI-failure postmortem ingestion)
