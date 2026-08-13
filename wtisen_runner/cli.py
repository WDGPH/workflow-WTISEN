from __future__ import annotations

import argparse
import json
import logging
import sys

from wtisen_runner.config import load_config
from wtisen_runner.logging import setup_logging
from wtisen_runner.orchestrator import run_all, run_single_stage_with_stats
from wtisen_runner.stages import determine_wtisen_dates
from wtisen_runner.storage.local_fs import LocalFilesystemStorage


EXIT_OK = 0
EXIT_USAGE_OR_CONFIG = 2
EXIT_PARTIAL_SUCCESS = 3
EXIT_STAGE_FAILURE = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wtisen-runner")
    sub = parser.add_subparsers(dest="command", required=True)

    run_all_cmd = sub.add_parser("run-all", help="Run determine->extract->transform->load")
    run_all_cmd.add_argument("--config", required=True)
    run_all_cmd.add_argument("--start")
    run_all_cmd.add_argument("--end")

    for name in ["determine", "extract", "transform", "load"]:
        p = sub.add_parser(name, help=f"Run {name} stage")
        p.add_argument("--config", required=True)
        if name == "extract":
            p.add_argument("--start")
            p.add_argument("--end")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE_OR_CONFIG) from exc

    setup_logging(debug=cfg.run.debug_sensitive_logging_enabled, json_logs=cfg.run.json_logs)

    if args.command in {"run-all", "extract"}:
        start = getattr(args, "start", None)
        end = getattr(args, "end", None)
        if (start is None) ^ (end is None):
            parser.error("--start and --end must be provided together")

    if args.command == "run-all":
        try:
            summary = run_all(cfg, start_override=args.start, end_override=args.end)
        except Exception as exc:
            logging.error("run-all failed: %s", exc, exc_info=True)
            raise SystemExit(EXIT_STAGE_FAILURE)
        print(json.dumps(summary.__dict__))
        if summary.partial_failure:
            raise SystemExit(EXIT_PARTIAL_SUCCESS)
        return

    if args.command == "determine":
        storage = LocalFilesystemStorage(cfg.storage.root_dir)
        rng = determine_wtisen_dates(cfg, storage)
        print(
            json.dumps(
                {
                    "stage": "determine",
                    "start": rng.start.isoformat(),
                    "end": rng.end.isoformat(),
                }
            )
        )
        return

    try:
        result = run_single_stage_with_stats(
            cfg,
            args.command,
            start=getattr(args, "start", None),
            end=getattr(args, "end", None),
        )
    except Exception as exc:
        logging.error("%s stage failed: %s", args.command, exc, exc_info=True)
        raise SystemExit(EXIT_STAGE_FAILURE)

    print(
        json.dumps(
            {
                "stage": args.command,
                "records": result.records,
                "files_total": result.files_total,
                "files_loaded": result.files_loaded,
                "files_skipped": result.files_skipped,
            }
        )
    )
    if result.files_skipped > 0:
        raise SystemExit(EXIT_PARTIAL_SUCCESS)

    return


if __name__ == "__main__":
    main()
