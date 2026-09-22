## `incident_evals` quality gate is now runnable instead of crashing

`incident_evals` was accepted by pipeline configuration (it was listed in
`VALID_GATE_NAMES`) but had no entry in `GateRunner`'s dispatch tables, so any
pipeline naming it failed at run time with `ValueError: Unsupported gate
name: 'incident_evals'` instead of at configuration validation.

`GateRunner` now dispatches `incident_evals` to the existing
`run_incident_eval_gate` implementation, which checks P0 incident-eval cases
under `src/bernstein/eval/cases/incidents/` for regression proof and blocks
merge on P0 cases missing it (#6156).

Hardening on top of the dispatch fix (#6165 review):

- The P0 proof marker (`.sdd/eval/incident_results/<case>.json`) is now
  resolved from the runner/project's trusted root, never from the agent
  worktree the incident corpus is read from - the worktree's `.sdd` is
  gitignored and disposable, and accepting proof from there let an agent
  satisfy the gate with an empty marker it wrote itself.
- `incident_evals` is now excluded from gate-result caching, since its
  verdict depends on `.sdd` state the changed-file cache key does not
  capture.
- An unreadable or non-UTF-8 incident case file no longer crashes the
  gate pipeline; the gate now returns the repository's `inconclusive`
  verdict instead.
- `incident_evals` is opt-in only: it has no `QualityGatesConfig` flag and
  is not part of the default pipeline. See
  [Incident-to-eval synthesis](../../eval/incident-synthesis.md) for how to
  enable it and what it checks.
