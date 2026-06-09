import json
from pathlib import Path

import polars as pl

from wtisen_runner.config import load_config
from wtisen_runner.stages import load_wtisen
from wtisen_runner.storage.local_fs import LocalFilesystemStorage


REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_SCHEMA = REPO_ROOT / "wtisen_runner" / "schemas" / "processed_wtisen_v2.0.json"


def _write_config(path: Path, root: Path):
    path.write_text(
        f"""
config_version: 1
source:
  url: "https://example"
  report_id: "r"
  phu_code: "2266"
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
storage:
  local:
    root_dir: "{root}"
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


def test_load_merges_and_archives(tmp_path):
    root = tmp_path / "data"
    cfg_path = tmp_path / "cfg.yaml"
    _write_config(cfg_path, root)
    cfg = load_config(str(cfg_path))
    storage = LocalFilesystemStorage(str(root))

    schema_path = PROCESSED_SCHEMA
    schema = json.loads(schema_path.read_text())
    columns = [f["name"] for f in schema["fields"]]

    row1 = {c: None for c in columns}
    row1.update(
        {
            "barcode": "A1",
            "date_collected": "2026-01-01",
            "date_received": "2026-01-02",
            "date_released": "2026-01-03",
            "date_reported": "2026-01-04",
            "city": "TORONTO",
        }
    )
    row2 = dict(row1)
    row2["city"] = "OTTAWA"

    pdir = storage.ensure_dir(cfg.storage.processed)
    pl.DataFrame([row1]).write_parquet(pdir / "one_v2.0.parquet")
    pl.DataFrame([row2]).write_parquet(pdir / "two_v2.0.parquet")

    loaded = load_wtisen(cfg, storage, schema_path)
    assert loaded == 2

    curated = pl.read_parquet(storage.resolve(cfg.storage.curated))
    assert curated.height == 1
    assert curated.get_column("city")[0] == "OTTAWA"

    archived = storage.list_files(cfg.storage.archive_processed, "*.parquet")
    assert len(archived) == 2
