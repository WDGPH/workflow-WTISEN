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
    assert cfg.source.phu_code == "<INSERT_PHU_CODE>"
    assert cfg.load.merge_keys


def test_config_rejects_invalid_phu(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
config_version: 1
source: {url: x, report_id: y, phu_code: abc, landing_file_prefix: wtisen}
auth: {username_env: PHO_USERNAME, password_env: PHO_PASSWORD}
run: {default_start_date: '2008-01-01', lookback_days: 3, archive_enabled: true, debug_sensitive_logging_enabled: false, json_logs: false, ignore_https_errors: false, login_timeout_ms: 30000, post_login_timeout_ms: 30000, report_viewer_timeout_ms: 120000, download_timeout_ms: 60000, download_retries: 2}
storage: {local: {root_dir: /tmp, landing: landing, processed: processed, archive_landing: al, archive_processed: ap, curated: curated/out.parquet}}
transform: {file_pattern: '*wtisen*.csv'}
load: {merge_keys: [barcode], file_pattern: '*_v2.0.parquet'}
"""
    )
    with pytest.raises(ValueError):
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
