import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from wtisen_runner import cli
from wtisen_runner.stages import StageResult


def _write_config(path):
    path.write_text(
        """
config_version: 1
source:
  url: "https://example"
  report_id: "r"
  phu_code: "0000"
  landing_file_prefix: "wtisen"
auth:
  username_env: "PHO_USERNAME"
  password_env: "PHO_PASSWORD"
run:
  default_start_date: "2008-01-01"
  lookback_days: 3
  archive_enabled: true
  debug_sensitive_logging_enabled: false
  json_logs: false
  ignore_https_errors: false
  login_timeout_ms: 30000
  post_login_timeout_ms: 30000
  report_viewer_timeout_ms: 120000
  download_timeout_ms: 60000
  download_retries: 2
  csv_direct_poll_max_attempts: 3
  csv_poll_request_timeout_ms: 25000
  csv_direct_poll_backoff_ms: [3000, 7000, 12000]
  csv_poll_warmup_on_first_transient: true
  csv_failure_probe_timeout_ms: 30000
storage:
  local:
    root_dir: "/tmp"
    landing: "landing"
    processed: "processed"
    archive_landing: "archive/landing"
    archive_processed: "archive/processed"
    curated: "curated/wtisen.parquet"
transform:
  file_pattern: "*wtisen*.csv"
load:
  merge_keys: [barcode, date_collected, date_received, date_released, date_reported]
  file_pattern: "*_v2.0.parquet"
"""
    )


def test_cli_rejects_partial_start_end_for_run_all(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    _write_config(cfg)
    monkeypatch.setattr(
        "sys.argv",
        ["wtisen-runner", "run-all", "--config", str(cfg), "--start", "2026-01-01"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_cli_rejects_partial_start_end_for_extract(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    _write_config(cfg)
    monkeypatch.setattr(
        "sys.argv",
        ["wtisen-runner", "extract", "--config", str(cfg), "--end", "2026-01-31"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_cli_determine_prints_start_end_json(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "cfg.yaml"
    _write_config(cfg)

    @dataclass(frozen=True)
    class _Range:
        start: object
        end: object

    from datetime import date

    monkeypatch.setattr(
        cli,
        "determine_wtisen_dates",
        lambda config, storage: _Range(start=date(2026, 1, 1), end=date(2026, 1, 31)),
    )
    monkeypatch.setattr("sys.argv", ["wtisen-runner", "determine", "--config", str(cfg)])

    cli.main()
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload == {"stage": "determine", "start": "2026-01-01", "end": "2026-01-31"}


def test_cli_transform_prints_stage_stats(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "cfg.yaml"
    _write_config(cfg)
    monkeypatch.setattr(
        cli,
        "run_single_stage_with_stats",
        lambda *args, **kwargs: StageResult(records=15, files_total=2, files_loaded=2, files_skipped=0),
    )
    monkeypatch.setattr("sys.argv", ["wtisen-runner", "transform", "--config", str(cfg)])

    cli.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {
        "stage": "transform",
        "records": 15,
        "files_total": 2,
        "files_loaded": 2,
        "files_skipped": 0,
    }


def test_cli_extract_partial_success_exit_code(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    _write_config(cfg)
    monkeypatch.setattr(
        cli,
        "run_single_stage_with_stats",
        lambda *args, **kwargs: StageResult(records=7, files_total=3, files_loaded=2, files_skipped=1),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["wtisen-runner", "extract", "--config", str(cfg), "--start", "2026-01-01", "--end", "2026-01-02"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == cli.EXIT_PARTIAL_SUCCESS


def test_cli_run_all_partial_success_exit_code(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    _write_config(cfg)
    monkeypatch.setattr(
        cli,
        "run_all",
        lambda *args, **kwargs: SimpleNamespace(
            start="2026-01-01",
            end="2026-01-31",
            extracted_records=5,
            transformed_records=4,
            loaded_records=4,
            extract={"records": 5, "files_total": 0, "files_loaded": 0, "files_skipped": 0},
            transform={"records": 4, "files_total": 2, "files_loaded": 1, "files_skipped": 1},
            load={"records": 4, "files_total": 1, "files_loaded": 1, "files_skipped": 0},
            partial_failure=True,
        ),
    )
    monkeypatch.setattr("sys.argv", ["wtisen-runner", "run-all", "--config", str(cfg)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == cli.EXIT_PARTIAL_SUCCESS


def test_cli_run_all_stage_failure_exit_code(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    _write_config(cfg)
    monkeypatch.setattr(cli, "run_all", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr("sys.argv", ["wtisen-runner", "run-all", "--config", str(cfg)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == cli.EXIT_STAGE_FAILURE
