## `incident_evals` quality gate is now runnable instead of crashing

`incident_evals` was accepted by pipeline configuration (it was listed in
`VALID_GATE_NAMES`) but had no entry in `GateRunner`'s dispatch tables, so any
pipeline naming it failed at run time with `ValueError: Unsupported gate
name: 'incident_evals'` instead of at configuration validation.

`GateRunner` now dispatches `incident_evals` to the existing
`run_incident_eval_gate` implementation, which checks P0 incident-eval cases
under `src/bernstein/eval/cases/incidents/` for regression proof and blocks
merge on P0 cases missing it (#6156).
