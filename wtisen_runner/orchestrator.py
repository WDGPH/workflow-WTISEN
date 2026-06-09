from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from wtisen_runner.config import WtisenConfig
from wtisen_runner.secrets import resolve_secret
from wtisen_runner.stages import (
    StageResult,
    determine_wtisen_dates,
    extract_wtisen,
    load_wtisen,
    load_wtisen_with_stats,
    transform_wtisen,
    transform_wtisen_with_stats,
)
from wtisen_runner.storage.local_fs import LocalFilesystemStorage


@dataclass(frozen=True)
class RunSummary:
    start: str
    end: str
    extracted_records: int
    transformed_records: int
    loaded_records: int
    extract: dict[str, int]
    transform: dict[str, int]
    load: dict[str, int]
    partial_failure: bool


def _resolve_run_window(
    config: WtisenConfig,
    storage: LocalFilesystemStorage,
    start_override: str | None,
    end_override: str | None,
) -> tuple[date, date]:
    if (start_override is None) ^ (end_override is None):
        raise ValueError("start and end overrides must be provided together")

    if start_override and end_override:
        start = date.fromisoformat(start_override)
        end = date.fromisoformat(end_override)
        if start > end:
            raise ValueError(f"Invalid date range ({start} > {end})")
        return start, end

    rng = determine_wtisen_dates(config, storage)
    return rng.start, rng.end


def _schema_paths() -> tuple[Path, Path]:
    base = Path(__file__).resolve().parent / "schemas"
    return (
        base / "landing_wtisen_v1.0.json",
        base / "processed_wtisen_v2.0.json",
    )


def run_all(config: WtisenConfig, start_override: str | None = None, end_override: str | None = None) -> RunSummary:
    storage = LocalFilesystemStorage(config.storage.root_dir)
    landing_schema_path, processed_schema_path = _schema_paths()
    start, end = _resolve_run_window(config, storage, start_override, end_override)

    username = resolve_secret(config.auth.username_env)
    password = resolve_secret(config.auth.password_env)

    extracted = extract_wtisen(config, storage, username, password, start=start, end=end)
    transformed = transform_wtisen_with_stats(config, storage, landing_schema_path, processed_schema_path)
    loaded = 0
    load_stats = {"records": 0, "files_total": 0, "files_loaded": 0, "files_skipped": 0}
    if transformed.records > 0:
        loaded_result = load_wtisen_with_stats(config, storage, processed_schema_path)
        loaded = loaded_result.records
        load_stats = {
            "records": loaded_result.records,
            "files_total": loaded_result.files_total,
            "files_loaded": loaded_result.files_loaded,
            "files_skipped": loaded_result.files_skipped,
        }
    else:
        logging.info("Skipping load stage because transform returned zero records")
        loaded = 0

    return RunSummary(
        start=start.isoformat(),
        end=end.isoformat(),
        extracted_records=extracted,
        transformed_records=transformed.records,
        loaded_records=loaded,
        extract={
            "records": extracted,
            "files_total": 0,
            "files_loaded": 0,
            "files_skipped": 0,
        },
        transform={
            "records": transformed.records,
            "files_total": transformed.files_total,
            "files_loaded": transformed.files_loaded,
            "files_skipped": transformed.files_skipped,
        },
        load=load_stats,
        partial_failure=(transformed.files_skipped > 0 or load_stats["files_skipped"] > 0),
    )


def run_single_stage(config: WtisenConfig, stage: str, start: str | None = None, end: str | None = None) -> int:
    return run_single_stage_with_stats(config, stage, start, end).records


def run_single_stage_with_stats(
    config: WtisenConfig,
    stage: str,
    start: str | None = None,
    end: str | None = None,
) -> StageResult:
    storage = LocalFilesystemStorage(config.storage.root_dir)
    landing_schema_path, processed_schema_path = _schema_paths()

    if stage == "determine":
        rng = determine_wtisen_dates(config, storage)
        logging.info("WTISEN extraction window: start=%s end=%s", rng.start, rng.end)
        return StageResult(records=0, files_total=0, files_loaded=0, files_skipped=0)

    if stage == "extract":
        start_dt, end_dt = _resolve_run_window(config, storage, start, end)
        username = resolve_secret(config.auth.username_env)
        password = resolve_secret(config.auth.password_env)
        records = extract_wtisen(config, storage, username, password, start_dt, end_dt)
        return StageResult(records=records, files_total=0, files_loaded=0, files_skipped=0)

    if stage == "transform":
        return transform_wtisen_with_stats(
            config,
            storage,
            landing_schema_path,
            processed_schema_path,
        )

    if stage == "load":
        return load_wtisen_with_stats(config, storage, processed_schema_path)

    raise ValueError(f"Unknown stage: {stage}")
