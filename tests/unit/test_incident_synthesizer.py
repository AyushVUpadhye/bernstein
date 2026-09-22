"""Tests for incident-to-eval synthesis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bernstein.core.tasks.dead_letter_queue import DeadLetterQueue
from bernstein.eval.incident_synthesizer import (
    IncidentEvalCase,
    IncidentSynthesizer,
    run_incident_eval_gate,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Severity routing
# ---------------------------------------------------------------------------


def _seed_dlq(
    workdir: Path,
    *,
    task_id: str = "T-1",
    title: str = "agent failure",
    role: str = "backend",
    reason: str = "max_retries_exhausted",
    error: str = "RuntimeError: boom",
    metadata: dict[str, object] | None = None,
) -> None:
    dlq = DeadLetterQueue(sdd_dir=workdir / ".sdd")
    dlq.enqueue(
        task_id=task_id,
        title=title,
        role=role,
        reason=reason,
        retry_count=3,
        original_error=error,
        metadata=metadata or {},
    )


class TestSeverityRouting:
    def test_prompt_injection_routes_to_p0(self, tmp_path: Path) -> None:
        _seed_dlq(
            tmp_path,
            reason="prompt_injection detected",
            metadata={"tags": ["prompt_injection"]},
        )
        synth = IncidentSynthesizer(tmp_path)
        result = synth.sync()
        assert len(result.created) == 1
        assert result.created[0].severity == "P0"

    def test_token_runaway_routes_to_p1(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path, reason="token_runaway")
        synth = IncidentSynthesizer(tmp_path)
        result = synth.sync()
        assert len(result.created) == 1
        assert result.created[0].severity == "P1"

    def test_unknown_reason_routes_to_p2(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path, reason="unexplained_flake")
        synth = IncidentSynthesizer(tmp_path)
        result = synth.sync()
        assert len(result.created) == 1
        assert result.created[0].severity == "P2"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_running_twice_does_not_duplicate(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path, reason="adapter_timeout")
        synth = IncidentSynthesizer(tmp_path)

        first = synth.sync()
        second = synth.sync()

        assert len(first.created) == 1
        assert len(second.created) == 0
        assert second.skipped_duplicates >= 1

        cases_dir = tmp_path / "src" / "bernstein" / "eval" / "cases" / "incidents"
        assert len(list(cases_dir.glob("inc-*.yaml"))) == 1

    def test_distinct_incidents_produce_distinct_ids(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path, task_id="T-A", reason="prompt_injection")
        _seed_dlq(tmp_path, task_id="T-B", reason="token_runaway")
        synth = IncidentSynthesizer(tmp_path)
        result = synth.sync()
        ids = {c.id for c in result.created}
        assert len(ids) == 2


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_emails_are_stripped_from_emitted_yaml(self, tmp_path: Path) -> None:
        # Use a non-allowlisted domain so the PII scanner actually flags it.
        _seed_dlq(
            tmp_path,
            error="ConnectionError: failed to reach alice@bigcorp.io via SMTP",
        )
        synth = IncidentSynthesizer(tmp_path)
        result = synth.sync()
        assert len(result.created) == 1

        cases_dir = tmp_path / "src" / "bernstein" / "eval" / "cases" / "incidents"
        body = next(cases_dir.glob("inc-*.yaml")).read_text(encoding="utf-8")
        assert "alice@bigcorp.io" not in body
        assert "***" in body

    def test_aws_key_is_redacted(self, tmp_path: Path) -> None:
        _seed_dlq(
            tmp_path,
            error="AccessDenied with key AKIAIOSFODNN7EXAMPLE returned 403",
        )
        synth = IncidentSynthesizer(tmp_path)
        result = synth.sync()
        assert len(result.created) == 1
        assert "AKIAIOSFODNN7EXAMPLE" not in result.created[0].prompt

    def test_dry_run_does_not_write_files(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path)
        synth = IncidentSynthesizer(tmp_path)
        result = synth.sync(dry_run=True)
        assert result.dry_run is True
        assert len(result.created) == 1
        cases_dir = tmp_path / "src" / "bernstein" / "eval" / "cases" / "incidents"
        assert not cases_dir.exists() or not list(cases_dir.glob("*.yaml"))


# ---------------------------------------------------------------------------
# YAML emission
# ---------------------------------------------------------------------------


class TestYamlEmission:
    def test_yaml_contains_required_fields(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path, reason="prompt_injection")
        synth = IncidentSynthesizer(tmp_path)
        synth.sync()

        cases_dir = tmp_path / "src" / "bernstein" / "eval" / "cases" / "incidents"
        body = next(cases_dir.glob("inc-*.yaml")).read_text(encoding="utf-8")
        for field_name in ("id:", "severity:", "prompt:", "expected_outcome:", "source_incident:"):
            assert field_name in body

    def test_yaml_id_starts_with_inc_prefix(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path)
        synth = IncidentSynthesizer(tmp_path)
        result = synth.sync()
        for case in result.created:
            assert case.id.startswith("inc-")
            assert len(case.id) == len("inc-") + 12


# ---------------------------------------------------------------------------
# Quality-gate entry point
# ---------------------------------------------------------------------------


class TestIncidentEvalGate:
    def test_no_cases_skips(self, tmp_path: Path) -> None:
        """No corpus is "nothing to check", not "checked, found nothing
        wrong" -- those are different claims for an offline audit receipt
        to make (#6165 review)."""
        status, _detail, counts = run_incident_eval_gate(tmp_path)
        assert status == "skipped"
        assert counts == {"P0": 0, "P1": 0, "P2": 0}

    def test_p0_without_proof_is_inconclusive(self, tmp_path: Path) -> None:
        """A P0 case with no proof marker means the harness never
        evaluated it -- that is ``inconclusive``, not ``fail`` (#6165
        review): ``fail`` would assert the case ran and regressed, which
        never happened."""
        _seed_dlq(tmp_path, reason="prompt_injection")
        IncidentSynthesizer(tmp_path).sync()
        status, detail, counts = run_incident_eval_gate(tmp_path)
        assert status == "inconclusive"
        assert "P0" in detail or counts["P0"] >= 1

    def test_p1_only_warns(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path, reason="token_runaway")
        IncidentSynthesizer(tmp_path).sync()
        status, _detail, counts = run_incident_eval_gate(tmp_path)
        assert status == "pass"
        assert counts["P1"] >= 1

    def test_workdir_defaults_to_run_dir_when_omitted(self, tmp_path: Path) -> None:
        """Back-compat: callers that pass a single root (the common case
        where the agent worktree and the runner's project root are the
        same directory) keep working unchanged.
        """
        _seed_dlq(tmp_path, reason="prompt_injection")
        IncidentSynthesizer(tmp_path).sync()
        status, _detail, _counts = run_incident_eval_gate(tmp_path)
        assert status == "inconclusive"

        results_dir = tmp_path / ".sdd" / "eval" / "incident_results"
        results_dir.mkdir(parents=True)
        case_stem = next((tmp_path / "src" / "bernstein" / "eval" / "cases" / "incidents").glob("inc-*.yaml")).stem
        (results_dir / f"{case_stem}.json").write_text("{}", encoding="utf-8")
        status, _detail, _counts = run_incident_eval_gate(tmp_path)
        assert status == "pass"

    def test_proof_resolved_from_workdir_not_run_dir(self, tmp_path: Path) -> None:
        """H1 (#6165 review): the incident corpus lives under ``run_dir``,
        but the trusted proof marker must be resolved from ``workdir`` --
        never from ``run_dir``, which is untracked state the agent under
        review controls.
        """
        run_dir = tmp_path / "worktree"
        workdir = tmp_path / "project"
        run_dir.mkdir()
        workdir.mkdir()
        _seed_dlq(run_dir, reason="prompt_injection")
        IncidentSynthesizer(run_dir).sync()
        case_stem = next((run_dir / "src" / "bernstein" / "eval" / "cases" / "incidents").glob("inc-*.yaml")).stem

        # An empty proof marker written only under run_dir must not pass.
        run_dir_results = run_dir / ".sdd" / "eval" / "incident_results"
        run_dir_results.mkdir(parents=True)
        (run_dir_results / f"{case_stem}.json").write_text("", encoding="utf-8")
        status, _detail, _counts = run_incident_eval_gate(run_dir, workdir)
        assert status == "inconclusive"

        # The same case passes once real proof exists under the trusted workdir.
        workdir_results = workdir / ".sdd" / "eval" / "incident_results"
        workdir_results.mkdir(parents=True)
        (workdir_results / f"{case_stem}.json").write_text("{}", encoding="utf-8")
        status, _detail, _counts = run_incident_eval_gate(run_dir, workdir)
        assert status == "pass"


# ---------------------------------------------------------------------------
# Direct API
# ---------------------------------------------------------------------------


class TestSynthesizeFromDlqEntry:
    def test_returns_case_for_normal_entry(self, tmp_path: Path) -> None:
        _seed_dlq(tmp_path)
        dlq = DeadLetterQueue(sdd_dir=tmp_path / ".sdd")
        entry = dlq.list_entries()[0]
        synth = IncidentSynthesizer(tmp_path)
        case = synth.synthesize_from_dlq_entry(entry)
        assert isinstance(case, IncidentEvalCase)
        assert case.source_incident == f"dlq:{entry.id}"
