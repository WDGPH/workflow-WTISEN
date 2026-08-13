# wtisen-runner

Standalone WTISEN runner that executes `determine -> extract -> transform -> load` without Kubeflow and without data-lake dependencies.

## What this package does
- Runs WTISEN extraction with Playwright using PHO credentials.
- Stores landing/processed/archive/curated artifacts on local filesystem paths.
- Keeps schema validation and merge-key dedupe/upsert behavior from the WTISEN pipeline design.
- Supports full run (`run-all`) and stage-by-stage commands.
- Resolves the default extraction end date using UTC.

## Container requirements
- Required env vars:
  - `PHO_USERNAME`
  - `PHO_PASSWORD`
- Required mounted config file:
  - `/config/wtisen.yaml`
- Required writable data mount:
  - `/data` (or match `storage.local.root_dir` in config)
- Runtime prerequisites:
  - outbound network access to WTISEN URLs
  - container user write permissions to data mount
  - system clock/timezone should be accurate (date windows are date-based)

## Quickstart (local Python)
```bash
cd /path/to/workflow-WTISEN
python -m venv .venv
. .venv/bin/activate
pip install -e .
pip install pytest
python -m playwright install --with-deps firefox
cp config/wtisen.example.yaml /tmp/wtisen.yaml
export PHO_USERNAME='your-user'
export PHO_PASSWORD='your-password'
wtisen-runner run-all --config /tmp/wtisen.yaml
```

Notes:
- The example config uses `storage.local.root_dir: "./data"`, which is appropriate for local Python runs from the repo root.
- If you run from another working directory, change `storage.local.root_dir` to an absolute path.
- On Linux, `python -m playwright install --with-deps firefox` is the preferred one-time setup because it installs the browser plus required system packages.

## Python script/module entrypoint
If you prefer invoking via Python directly (instead of console script):
```bash
cd /path/to/workflow-WTISEN
python -m wtisen_runner.cli run-all --config /tmp/wtisen.yaml
```

## Docker build
```bash
cd /path/to/workflow-WTISEN
docker build -t wtisen-runner:local .
```

## Docker run
```bash
docker run --rm \
  -e PHO_USERNAME='your-user' \
  -e PHO_PASSWORD='your-password' \
  -v /absolute/path/wtisen.yaml:/config/wtisen.yaml:ro \
  -v /absolute/path/data:/data \
  wtisen-runner:local run-all --config /config/wtisen.yaml
```

## Cron scheduling (host cron + Docker)
Example nightly run at 01:30 UTC:
```cron
CRON_TZ=UTC
30 1 * * * /usr/bin/docker run --rm -e PHO_USERNAME="$PHO_USERNAME" -e PHO_PASSWORD="$PHO_PASSWORD" -v /opt/wtisen/config/wtisen.yaml:/config/wtisen.yaml:ro -v /opt/wtisen/data:/data wtisen-runner:local run-all --config /config/wtisen.yaml >> /var/log/wtisen-runner.log 2>&1
```

Notes:
- Ensure `PHO_USERNAME` and `PHO_PASSWORD` are available to cron (for example via `/etc/environment` or wrapper script).
- Ensure the config mounted at `/config/wtisen.yaml` sets `storage.local.root_dir: "/data"` so output is written to the mounted volume.
- Set `CRON_TZ=UTC` in the crontab or run the host in UTC so the schedule time matches the documented UTC window.
- Use absolute paths for all mounts and logs.
- Prefer a small wrapper script if you need additional setup such as `PATH`, environment loading, or log rotation.
- Exit code `3` means partial success; alerting/monitoring should treat this as non-healthy.

## Cron scheduling (host cron + local Python, no Docker)
Example nightly run at 01:30 UTC with a project virtualenv:
```cron
CRON_TZ=UTC
30 1 * * * cd /opt/workflow-WTISEN && PHO_USERNAME="$PHO_USERNAME" PHO_PASSWORD="$PHO_PASSWORD" /opt/workflow-WTISEN/.venv/bin/python -m wtisen_runner.cli run-all --config /opt/wtisen/config/wtisen.yaml >> /var/log/wtisen-runner.log 2>&1
```

Notes:
- Install dependencies and browser binaries once in that environment: `pip install -e .`, `pip install pytest`, and `python -m playwright install --with-deps firefox`.
- If you cannot use `--with-deps` on the host, install the required Linux system packages separately before running `python -m playwright install firefox`.
- Set `CRON_TZ=UTC` in the crontab or run the host in UTC so the schedule time matches the documented UTC window.
- Use absolute paths for the config file and `storage.local.root_dir` because cron should not rely on an implicit working directory.
- If you do not `cd` into the repo, the config should not use `./data`; set `storage.local.root_dir` to an absolute path such as `/opt/wtisen/data`.

## k3s scheduling (Kubernetes CronJob)
The image entrypoint is already `wtisen-runner`, so `args` can directly provide subcommands.

Example resources:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: wtisen-credentials
type: Opaque
stringData:
  PHO_USERNAME: "your-user"
  PHO_PASSWORD: "your-password"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: wtisen-config
data:
  wtisen.yaml: |
    config_version: 1
    source:
      url: "<INSERT_SITE_URL>"
      report_id: "<INSERT_REPORT_ID>"
      phu_code: "<INSERT_PHU_CODE>"
      landing_file_prefix: "wtisen"
    auth:
      username_env: "PHO_USERNAME"
      password_env: "PHO_PASSWORD"
    run:
      default_start_date: "2008-01-01"
      lookback_days: 3
      archive_enabled: true
      json_logs: true
      debug_sensitive_logging_enabled: false
      ignore_https_errors: false
      login_timeout_ms: 30000
      post_login_timeout_ms: 30000
      report_viewer_timeout_ms: 120000
      download_timeout_ms: 60000
      download_retries: 2
    storage:
      local:
        root_dir: "/data"
        landing: "landing/wtisen"
        processed: "processed/wtisen"
        archive_landing: "archive/landing/wtisen"
        archive_processed: "archive/processed/wtisen"
        curated: "curated/wtisen/wtisen_curated_v2.0.parquet"
    transform:
      file_pattern: "*wtisen*.csv"
    load:
      merge_keys:
        - barcode
        - date_collected
        - date_received
        - date_released
        - date_reported
      file_pattern: "*_v2.0.parquet"
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: wtisen-runner
spec:
  schedule: "30 1 * * *"
  timeZone: "Etc/UTC"
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 1800
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: wtisen-runner
              image: wtisen-runner:local
              args: ["run-all", "--config", "/config/wtisen.yaml"]
              envFrom:
                - secretRef:
                    name: wtisen-credentials
              volumeMounts:
                - name: config
                  mountPath: /config
                  readOnly: true
                - name: data
                  mountPath: /data
          volumes:
            - name: config
              configMap:
                name: wtisen-config
            - name: data
              persistentVolumeClaim:
                claimName: wtisen-data-pvc
```

Save the manifest above as `wtisen-cronjob.yaml`, then apply it:
```bash
kubectl apply -f wtisen-cronjob.yaml
```

Notes:
- Keep `storage.local.root_dir: "/data"` in the mounted config so the job writes to the PVC.
- `timeZone: "Etc/UTC"` makes the schedule match the documented UTC run time explicitly.
- Replace `wtisen-runner:local` with a registry-qualified image for shared environments, or import the built image onto each k3s node if you are running it locally.
- Publishing the image is deployment-specific and is typically handled by the team operating the cluster, whether that image comes from GitHub Actions or a local build.
- `startingDeadlineSeconds` limits how long Kubernetes will try to catch up a missed schedule after controller downtime.

## Stage-specific commands
```bash
wtisen-runner determine --config /config/wtisen.yaml
wtisen-runner extract --config /config/wtisen.yaml --start 2026-01-01 --end 2026-01-31
wtisen-runner transform --config /config/wtisen.yaml
wtisen-runner load --config /config/wtisen.yaml
```

## Command contract
- `run-all`:
  - Optional `--start`/`--end` overrides are accepted only when both are provided.
  - If neither is provided, date window is derived by `determine`.
- `extract`:
  - Optional `--start`/`--end` overrides are accepted only when both are provided.
  - If neither is provided, date window is derived by `determine`.
- `determine`:
  - Prints JSON with computed date window:
    - `{"stage":"determine","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}`
- `transform` / `load`:
  - Return stage stats as JSON:
    - `{"stage":"transform","records":N,"files_total":X,"files_loaded":Y,"files_skipped":Z}`

## Config contract
See `config/wtisen.example.yaml`.

Required groups:
- `source`: WTISEN URL/report/phu/prefix
- `auth`: env key names for username/password
- `run`: incremental controls and logging flags
  - includes extract resiliency knobs:
    - `login_timeout_ms`
    - `post_login_timeout_ms`
    - `report_viewer_timeout_ms`
    - `download_timeout_ms`
    - `download_retries`
- `storage.local`: root + landing/processed/archive/curated paths
- `transform`: landing file pattern
- `load`: merge keys and processed file pattern

## Data storage behavior
All storage paths are relative to `storage.local.root_dir`.

Using the container-oriented config values shown above:
- `root_dir`: `/data`
- `landing`: `landing/wtisen`
- `processed`: `processed/wtisen`
- `archive_landing`: `archive/landing/wtisen`
- `archive_processed`: `archive/processed/wtisen`
- `curated`: `curated/wtisen/wtisen_curated_v2.0.parquet`

Effective locations are:
- landing input/output folder: `/data/landing/wtisen`
- processed output folder: `/data/processed/wtisen`
- landing archive folder: `/data/archive/landing/wtisen`
- processed archive folder: `/data/archive/processed/wtisen`
- curated parquet target: `/data/curated/wtisen/wtisen_curated_v2.0.parquet`

The shipped local example in `config/wtisen.example.yaml` instead uses `storage.local.root_dir: "./data"`.

Stage behavior:
- `extract`:
  - Writes WTISEN CSV files into `landing`.
  - Empty downloads (no data rows) are not written.
- `transform`:
  - Reads matching CSV files from `landing`.
  - Writes processed parquet files to `processed`.
  - If `run.archive_enabled: true`, successfully transformed landing files are moved from `landing` to `archive_landing`.
  - If `run.archive_enabled: false`, landing files remain in `landing`.
- `load`:
  - Reads matching parquet files from `processed`.
  - Merges into one local curated parquet at `curated` using `load.merge_keys` (`keep="last"` on duplicates).
  - If `run.archive_enabled: true`, successfully loaded processed files are moved from `processed` to `archive_processed`.
  - If `run.archive_enabled: false`, processed files remain in `processed`.

Operational notes:
- Archiving applies only to files that were successfully transformed/loaded.
- Failed files are left in place and counted as skipped.
- With archiving disabled, reruns can reprocess the same inputs unless you clean up or narrow file patterns.

## Logging and sensitive data
- Default logs are plain text; set `run.json_logs: true` for JSON lines.
- Keep `run.debug_sensitive_logging_enabled: false` in shared environments.
- Authentication/exception messages are sanitized before logging where possible.

## TLS behavior
- Default is strict TLS verification (`run.ignore_https_errors: false`).
- Set `run.ignore_https_errors: true` only if your organization explicitly accepts the risk (for example, known internal TLS interception or non-public CA chains).

## Failure modes
- Missing config fields: startup validation failure.
- Missing credentials: extraction fails before browser automation.
- No matching landing/processed files: transform/load safely skip with zero records.
- Stage-level file/schema errors are logged per file and processing continues.
- Transform/load fail the stage only if all candidate files fail.

## Exit codes
- `0`: success (including no-op when no matching files are found).
- `2`: usage/config error (bad args or config validation failure).
- `3`: partial success (command completed but one or more files were skipped/failed).
- `4`: stage failure (stage execution failed before usable completion).

Scheduler handling recommendation:
- Treat `0` as healthy.
- Treat `2` and `4` as failures with retry/escalation.
- Treat `3` as warning/partial failure (run completed but needs review).

## Output semantics
- `run-all` prints a summary JSON payload:
  - `start`, `end`, `extracted_records`, `transformed_records`, `loaded_records`.
  - per-stage stats: `extract`, `transform`, `load` with:
    - `records`, `files_total`, `files_loaded`, `files_skipped`
  - `partial_failure` (boolean)
- Local load behavior writes/updates a curated parquet file using merge-key dedupe (`keep="last"`).
- This is not a transactional Delta merge; it is local filesystem parquet consolidation.

## Tests
```bash
cd /path/to/workflow-WTISEN
. .venv/bin/activate
pytest -q
```

## Notes
- v1 storage backend is local filesystem only.
- Existing Kubeflow WTISEN pipeline is not modified by this package.
