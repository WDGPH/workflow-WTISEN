from pathlib import Path

import pytest

from wtisen_runner.config import load_config
from wtisen_runner.stages import determine_wtisen_dates
from wtisen_runner.storage.local_fs import LocalFilesystemStorage


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO_ROOT / "config" / "wtisen.example.yaml"


def test_config_loads_example():
    cfg = load_config(str(EXAMPLE_CONFIG))
    assert cfg.config_version == 1
    assert cfg.source.phu_code == "0000"
    assert cfg.load.merge_keys


def test_config_rejects_invalid_phu(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
config_version: 1
source: {url: x, report_id: y, phu_code: abc, landing_file_prefix: wtisen}
auth: {username_env: PHO_USERNAME, password_env: PHO_PASSWORD}
run: {default_start_date: '2008-01-01', lookback_days: 3, archive_enabled: true, debug_sensitive_logging_enabled: false, json_logs: false, ignore_https_errors: false, login_timeout_ms: 30000, post_login_timeout_ms: 30000, report_viewer_timeout_ms: 120000, download_timeout_ms: 60000, download_retries: 2, csv_direct_poll_max_attempts: 3, csv_poll_request_timeout_ms: 25000, csv_direct_poll_backoff_ms: [3000, 7000, 12000], csv_poll_warmup_on_first_transient: true, csv_failure_probe_timeout_ms: 30000}
storage: {local: {root_dir: /tmp, landing: landing, processed: processed, archive_landing: al, archive_processed: ap, curated: curated/out.parquet}}
transform: {file_pattern: '*wtisen*.csv'}
load: {merge_keys: [barcode], file_pattern: '*_v2.0.parquet'}
"""
    )
    with pytest.raises(ValueError):
        load_config(str(p))


_VALID_RUN = (
    "default_start_date: '2008-01-01'\n"
    "lookback_days: 3\n"
    "archive_enabled: true\n"
    "debug_sensitive_logging_enabled: false\n"
    "json_logs: false\n"
    "ignore_https_errors: false\n"
    "login_timeout_ms: 30000\n"
    "post_login_timeout_ms: 30000\n"
    "report_viewer_timeout_ms: 120000\n"
    "download_timeout_ms: 60000\n"
    "download_retries: 2\n"
    "csv_direct_poll_max_attempts: 3\n"
    "csv_poll_request_timeout_ms: 25000\n"
    "csv_direct_poll_backoff_ms: [3000, 7000, 12000]\n"
    "csv_poll_warmup_on_first_transient: true\n"
    "csv_failure_probe_timeout_ms: 30000\n"
)
_VALID_REST = (
    "source: {url: x, report_id: y, phu_code: '0000', landing_file_prefix: wtisen}\n"
    "auth: {username_env: PHO_USERNAME, password_env: PHO_PASSWORD}\n"
    "storage: {local: {root_dir: /tmp, landing: landing, processed: processed, "
    "archive_landing: al, archive_processed: ap, curated: curated/out.parquet}}\n"
    "transform: {file_pattern: '*wtisen*.csv'}\n"
    "load: {merge_keys: [barcode], file_pattern: '*_v2.0.parquet'}\n"
)


def _make_config(tmp_path, run_overrides: str = "") -> str:
    p = tmp_path / "cfg.yaml"
    run_block = _VALID_RUN + run_overrides
    p.write_text(f"config_version: 1\n{_VALID_REST}run:\n" + "".join(f"  {line}\n" for line in run_block.splitlines()))
    return str(p)


def test_config_loads_new_csv_poll_fields(tmp_path):
    cfg = load_config(_make_config(tmp_path))
    assert cfg.run.csv_direct_poll_max_attempts == 3
    assert cfg.run.csv_poll_request_timeout_ms == 25000
    assert cfg.run.csv_direct_poll_backoff_ms == [3000, 7000, 12000]
    assert cfg.run.csv_poll_warmup_on_first_transient is True
    assert cfg.run.csv_failure_probe_timeout_ms == 30000


def test_config_rejects_empty_backoff_list(tmp_path):
    p = tmp_path / "bad.yaml"
    run_lines = _VALID_RUN.replace("csv_direct_poll_backoff_ms: [3000, 7000, 12000]", "csv_direct_poll_backoff_ms: []")
    p.write_text(f"config_version: 1\n{_VALID_REST}run:\n" + "".join(f"  {line}\n" for line in run_lines.splitlines()))
    with pytest.raises(ValueError, match="csv_direct_poll_backoff_ms"):
        load_config(str(p))


def test_config_rejects_non_int_backoff_list(tmp_path):
    p = tmp_path / "bad.yaml"
    run_lines = _VALID_RUN.replace("csv_direct_poll_backoff_ms: [3000, 7000, 12000]", "csv_direct_poll_backoff_ms: [3000, bad, 12000]")
    p.write_text(f"config_version: 1\n{_VALID_REST}run:\n" + "".join(f"  {line}\n" for line in run_lines.splitlines()))
    with pytest.raises((ValueError, Exception)):
        load_config(str(p))


def test_config_rejects_zero_csv_poll_max_attempts(tmp_path):
    p = tmp_path / "bad.yaml"
    run_lines = _VALID_RUN.replace("csv_direct_poll_max_attempts: 3", "csv_direct_poll_max_attempts: 0")
    p.write_text(f"config_version: 1\n{_VALID_REST}run:\n" + "".join(f"  {line}\n" for line in run_lines.splitlines()))
    with pytest.raises(ValueError, match="csv_direct_poll_max_attempts"):
        load_config(str(p))


def test_determine_uses_utc_date(tmp_path, monkeypatch):
    cfg = load_config(str(EXAMPLE_CONFIG))

    class _FakeNow:
        @staticmethod
        def date():
            from datetime import date

            return date(2026, 6, 9)

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return _FakeNow()

    monkeypatch.setattr("wtisen_runner.stages.datetime", _FakeDatetime)

    rng = determine_wtisen_dates(cfg, LocalFilesystemStorage(str(tmp_path)))
    assert rng.end.isoformat() == "2026-06-09"
