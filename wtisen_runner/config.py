from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SourceConfig:
    url: str
    report_id: str
    phu_code: str
    landing_file_prefix: str


@dataclass(frozen=True)
class AuthConfig:
    username_env: str
    password_env: str


@dataclass(frozen=True)
class RunConfig:
    default_start_date: str
    lookback_days: int
    archive_enabled: bool
    debug_sensitive_logging_enabled: bool
    json_logs: bool
    ignore_https_errors: bool
    login_timeout_ms: int
    post_login_timeout_ms: int
    report_viewer_timeout_ms: int
    download_timeout_ms: int
    download_retries: int


@dataclass(frozen=True)
class LocalStorageConfig:
    root_dir: str
    landing: str
    processed: str
    archive_landing: str
    archive_processed: str
    curated: str


@dataclass(frozen=True)
class TransformConfig:
    file_pattern: str


@dataclass(frozen=True)
class LoadConfig:
    merge_keys: list[str]
    file_pattern: str


@dataclass(frozen=True)
class WtisenConfig:
    config_version: int
    source: SourceConfig
    auth: AuthConfig
    run: RunConfig
    storage: LocalStorageConfig
    transform: TransformConfig
    load: LoadConfig


def _require_string(obj: dict, key: str, label: str | None = None) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label or key} must be a non-empty string")
    return value


def _require_bool(obj: dict, key: str, default: bool | None = None, label: str | None = None) -> bool:
    value = obj.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{label or key} must be a boolean")
    return value


def _require_int(obj: dict, key: str, label: str | None = None) -> int:
    value = obj.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{label or key} must be an integer")
    return value


def load_config(path: str) -> WtisenConfig:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("Config root must be a mapping")

    config_version = _require_int(data, "config_version")
    if config_version != 1:
        raise ValueError(f"Unsupported config_version={config_version}; expected 1")

    source = data.get("source") or {}
    auth = data.get("auth") or {}
    run = data.get("run") or {}
    storage = ((data.get("storage") or {}).get("local")) or {}
    transform = data.get("transform") or {}
    load = data.get("load") or {}

    cfg = WtisenConfig(
        config_version=config_version,
        source=SourceConfig(
            url=_require_string(source, "url", label="source.url"),
            report_id=_require_string(source, "report_id", label="source.report_id"),
            phu_code=_require_string(source, "phu_code", label="source.phu_code"),
            landing_file_prefix=_require_string(
                source,
                "landing_file_prefix",
                label="source.landing_file_prefix",
            ),
        ),
        auth=AuthConfig(
            username_env=_require_string(auth, "username_env", label="auth.username_env"),
            password_env=_require_string(auth, "password_env", label="auth.password_env"),
        ),
        run=RunConfig(
            default_start_date=_require_string(run, "default_start_date", label="run.default_start_date"),
            lookback_days=_require_int(run, "lookback_days", label="run.lookback_days"),
            archive_enabled=_require_bool(run, "archive_enabled", default=True, label="run.archive_enabled"),
            debug_sensitive_logging_enabled=_require_bool(
                run,
                "debug_sensitive_logging_enabled",
                default=False,
                label="run.debug_sensitive_logging_enabled",
            ),
            json_logs=_require_bool(run, "json_logs", default=False, label="run.json_logs"),
            ignore_https_errors=_require_bool(
                run,
                "ignore_https_errors",
                default=False,
                label="run.ignore_https_errors",
            ),
            login_timeout_ms=_require_int(run, "login_timeout_ms", label="run.login_timeout_ms"),
            post_login_timeout_ms=_require_int(
                run,
                "post_login_timeout_ms",
                label="run.post_login_timeout_ms",
            ),
            report_viewer_timeout_ms=_require_int(
                run,
                "report_viewer_timeout_ms",
                label="run.report_viewer_timeout_ms",
            ),
            download_timeout_ms=_require_int(run, "download_timeout_ms", label="run.download_timeout_ms"),
            download_retries=_require_int(run, "download_retries", label="run.download_retries"),
        ),
        storage=LocalStorageConfig(
            root_dir=_require_string(storage, "root_dir", label="storage.local.root_dir"),
            landing=_require_string(storage, "landing", label="storage.local.landing"),
            processed=_require_string(storage, "processed", label="storage.local.processed"),
            archive_landing=_require_string(storage, "archive_landing", label="storage.local.archive_landing"),
            archive_processed=_require_string(
                storage,
                "archive_processed",
                label="storage.local.archive_processed",
            ),
            curated=_require_string(storage, "curated", label="storage.local.curated"),
        ),
        transform=TransformConfig(
            file_pattern=_require_string(transform, "file_pattern", label="transform.file_pattern"),
        ),
        load=LoadConfig(
            merge_keys=load.get("merge_keys") or [],
            file_pattern=_require_string(load, "file_pattern", label="load.file_pattern"),
        ),
    )

    if len(cfg.source.phu_code) != 4 or not cfg.source.phu_code.isnumeric():
        raise ValueError("source.phu_code must be exactly 4 numeric characters")
    if cfg.run.lookback_days < 0:
        raise ValueError("run.lookback_days must be >= 0")
    if cfg.run.login_timeout_ms <= 0:
        raise ValueError("run.login_timeout_ms must be > 0")
    if cfg.run.post_login_timeout_ms <= 0:
        raise ValueError("run.post_login_timeout_ms must be > 0")
    if cfg.run.report_viewer_timeout_ms <= 0:
        raise ValueError("run.report_viewer_timeout_ms must be > 0")
    if cfg.run.download_timeout_ms <= 0:
        raise ValueError("run.download_timeout_ms must be > 0")
    if cfg.run.download_retries < 1:
        raise ValueError("run.download_retries must be >= 1")
    if not cfg.load.merge_keys or not all(isinstance(k, str) for k in cfg.load.merge_keys):
        raise ValueError("load.merge_keys must be a non-empty list of strings")
    return cfg
