from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

import polars as pl
from frictionless import Resource, Schema, system
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from wtisen_runner.config import WtisenConfig
from wtisen_runner.logging import sanitize_text
from wtisen_runner.storage.local_fs import LocalFilesystemStorage


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date


@dataclass(frozen=True)
class StageResult:
    records: int
    files_total: int
    files_loaded: int
    files_skipped: int


def _parse_range_date(token: str) -> date | None:
    try:
        return datetime.strptime(token, "%Y%m%d").date()
    except ValueError:
        return None


def _extract_date_range(filename: str) -> tuple[date, date] | None:
    date_tokens = re.findall(r"(?<!\d)(\d{8})(?!\d)", filename)
    for left, right in zip(date_tokens, date_tokens[1:]):
        start_date = _parse_range_date(left)
        end_date = _parse_range_date(right)
        if start_date and end_date and start_date <= end_date:
            return (start_date, end_date)
    return None


def _file_order_key(filename: str) -> tuple[int, date, date, str]:
    parsed = _extract_date_range(filename)
    if parsed is not None:
        return (0, parsed[0], parsed[1], filename.lower())
    return (1, date.max, date.max, filename.lower())


def determine_wtisen_dates(config: WtisenConfig, storage: LocalFilesystemStorage) -> DateRange:
    end_date = datetime.now(timezone.utc).date()
    start_default = date.fromisoformat(config.run.default_start_date)

    curated_path = storage.resolve(config.storage.curated)
    latest = None
    if curated_path.exists():
        try:
            df = pl.read_parquet(curated_path)
            candidates: list[date] = []
            for col in ["date_collected", "date_received", "date_released", "date_reported"]:
                if col not in df.columns:
                    continue
                series = df.get_column(col).drop_nulls()
                if series.is_empty():
                    continue
                v = series.max()
                if isinstance(v, datetime):
                    candidates.append(v.date())
                elif isinstance(v, date):
                    candidates.append(v)
                elif v is not None:
                    candidates.append(datetime.fromisoformat(str(v)).date())
            if candidates:
                latest = max(candidates)
        except Exception:
            logging.info("Could not read curated output; using default start date")

    if latest is None:
        start_date = start_default
    else:
        start_date = latest - timedelta(days=config.run.lookback_days)
    if start_date > end_date:
        start_date = end_date

    return DateRange(start=start_date, end=end_date)


def extract_wtisen(
    config: WtisenConfig,
    storage: LocalFilesystemStorage,
    username: str,
    password: str,
    start: date,
    end: date,
) -> int:
    def count_data_rows(csv_bytes: bytes) -> int:
        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig", errors="replace")))
        for _ in range(3):
            next(reader, None)
        if next(reader, None) is None:
            return 0
        return sum(1 for row in reader if any(str(cell).strip() for cell in row))

    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", config.source.landing_file_prefix).strip("_") or "wtisen"
    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    home_url = f"{config.source.url.rstrip('/')}/home.aspx"
    login_host = "login.microsoftonline.com"
    report_path = f"{urlsplit(config.source.url).path.rstrip('/')}/RSReports/{unquote_plus(config.source.report_id)}"
    viewer_url = (
        f"{config.source.url}/_layouts/15/ReportServer/RSViewerPage.aspx?"
        f"{urlencode({'rv:RelativeReportUrl': report_path})}"
    )

    windows = []
    cur = start
    while cur <= end:
        cur_end = min(cur + timedelta(days=3 * 365), end)
        windows.append((cur, cur_end))
        cur = cur_end + timedelta(days=1)

    files_written = 0
    total_rows = 0
    recovery_metrics = {
        "first_csv_504_count": 0,
        "html_recovery_200_count": 0,
        "second_csv_200_count": 0,
        "csv_recovery_count": 0,
        "csv_recovery_latency_ms_total": 0,
    }
    logging.info(
        "WTISEN extractor config: download_retries=%s "
        "report_viewer_timeout_ms=%s login_timeout_ms=%s "
        "csv_direct_poll_max_attempts=%s csv_poll_request_timeout_ms=%s "
        "csv_direct_poll_backoff_ms=%s csv_poll_warmup_on_first_transient=%s "
        "csv_failure_probe_timeout_ms=%s",
        config.run.download_retries,
        config.run.report_viewer_timeout_ms,
        config.run.login_timeout_ms,
        config.run.csv_direct_poll_max_attempts,
        config.run.csv_poll_request_timeout_ms,
        config.run.csv_direct_poll_backoff_ms,
        config.run.csv_poll_warmup_on_first_transient,
        config.run.csv_failure_probe_timeout_ms,
    )

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = None
        page = None
        try:
            context = browser.new_context(
                ignore_https_errors=config.run.ignore_https_errors,
                accept_downloads=True,
            )
            context.clear_cookies()
            page = context.new_page()

            email_input = page.locator("input[name='loginfmt']").first
            passwd_input = page.locator("input[name='passwd']").first

            class AuthCategory:
                USERNAME_REJECTED = "USERNAME_REJECTED"
                PASSWORD_REJECTED = "PASSWORD_REJECTED"
                PASSWORD_PROMPT_TIMEOUT = "PASSWORD_PROMPT_TIMEOUT"
                SIGNIN_REJECTED = "SIGNIN_REJECTED"
                MFA_PROMPT_UNRECOGNIZED = "MFA_PROMPT_UNRECOGNIZED"
                STILL_ON_LOGIN_FORM = "STILL_ON_LOGIN_FORM"
                POST_LOGIN_NAVIGATION_TIMEOUT = "POST_LOGIN_NAVIGATION_TIMEOUT"

            log_sensitive_debug = config.run.debug_sensitive_logging_enabled

            def sanitize_url(raw_url: str) -> str:
                try:
                    parts = urlsplit(raw_url or "")
                    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
                except Exception:
                    return "<invalid-url>"

            def raise_auth_error(
                category: str,
                detail: str | None = None,
                cause: Exception | None = None,
            ) -> None:
                safe_detail = f" detail={sanitize_text(detail)}" if detail else ""
                err = RuntimeError(f"WTISEN_AUTH_FAILURE category={category}{safe_detail}")
                if cause is not None:
                    raise err from cause
                raise err

            def is_visible(locator, timeout: int = 1500) -> bool:
                try:
                    locator.wait_for(state="visible", timeout=timeout)
                    return True
                except PlaywrightTimeoutError:
                    return False

            def read_identity_error() -> str | None:
                alert = page.locator('div[role="alert"][aria-live="assertive"]').first
                if not is_visible(alert, timeout=1200):
                    return None
                text = alert.inner_text().strip()
                return text or None

            def page_title() -> str:
                try:
                    return page.title()
                except Exception:
                    return "<unavailable>"

            def log_page_state_debug(stage: str) -> None:
                if not logging.getLogger().isEnabledFor(logging.DEBUG):
                    return
                logging.debug(
                    "%s | url=%s | title=%r",
                    stage,
                    sanitize_url(page.url),
                    page_title(),
                )

            def read_page_html() -> str | None:
                try:
                    return page.content()
                except Exception:
                    return None

            def log_failure_page_state_debug(stage: str) -> None:
                log_page_state_debug(stage)
                if not log_sensitive_debug:
                    return

                html = read_page_html()
                if html is None:
                    logging.debug("%s | page content unavailable", stage)
                    return

                snippet = sanitize_text(html[:800])
                logging.debug("%s | html_snippet=%r", stage, snippet)

            def click_button(patterns: list[str]) -> str | None:
                for pattern in patterns:
                    regex = re.compile(pattern, flags=re.IGNORECASE)
                    button = page.get_by_role("button", name=regex).first
                    if is_visible(button, timeout=700):
                        label = button.inner_text().strip() or pattern
                        button.click()
                        return label
                return None

            def format_response_body_snippet(response, max_chars: int) -> str:
                body_snippet = re.sub(r"\s+", " ", response.text()).strip()
                if len(body_snippet) > max_chars:
                    body_snippet = f"{body_snippet[:max_chars]}... [truncated]"
                return body_snippet

            def build_warmup_url(download_url: str) -> str:
                warmup_url = download_url
                if "rs%3AFormat=CSV" in warmup_url:
                    warmup_url = warmup_url.replace("rs%3AFormat=CSV", "rs%3AFormat=HTML4.0", 1)
                elif "rs:Format=CSV" in warmup_url:
                    warmup_url = warmup_url.replace("rs:Format=CSV", "rs:Format=HTML4.0", 1)
                else:
                    warmup_url = f"{warmup_url}&rs%3AFormat=HTML4.0"
                if "rc%3AToolbar=" not in warmup_url and "rc:Toolbar=" not in warmup_url:
                    warmup_url = f"{warmup_url}&rc%3AToolbar=false"
                if "rc%3AParameters=" not in warmup_url and "rc:Parameters=" not in warmup_url:
                    warmup_url = f"{warmup_url}&rc%3AParameters=false"
                return warmup_url

            def run_warmup_request(dl_url: str, batch_label: str, attempt: int, timeout_ms: int) -> int | None:
                warmup_url = build_warmup_url(dl_url)
                try:
                    response = context.request.get(
                        warmup_url,
                        timeout=timeout_ms,
                        fail_on_status_code=False,
                    )
                    body_snippet = format_response_body_snippet(response, max_chars=500)
                    log_fn = logging.info if response.status < 400 else logging.warning
                    log_fn(
                        "Batch %s attempt %s warmup response: status=%s server=%r content-type=%r body_snippet=%r",
                        batch_label,
                        attempt,
                        response.status,
                        response.headers.get("server"),
                        response.headers.get("content-type"),
                        body_snippet,
                    )
                    warmup_content_type = (response.headers.get("content-type") or "").lower()
                    warmup_is_html4 = (
                        "rs%3AFormat=HTML4.0" in warmup_url or "rs:Format=HTML4.0" in warmup_url
                    )
                    if int(response.status) == 200 and warmup_is_html4 and "text/html" in warmup_content_type:
                        recovery_metrics["html_recovery_200_count"] += 1
                        logging.info(
                            "WTISEN metric html_recovery_200_count incremented: %s",
                            recovery_metrics["html_recovery_200_count"],
                        )
                    return int(response.status)
                except Exception as err:
                    logging.warning(
                        "Batch %s attempt %s warmup request failed (continuing): %s: %s",
                        batch_label,
                        attempt,
                        type(err).__name__,
                        err,
                    )
                    return None

            def fetch_csv_with_polling(dl_url: str, batch_label: str, attempt: int) -> bytes | None:
                transient_statuses = {502, 503, 504}
                warmup_used = False
                first_csv_504_monotonic = None
                for poll_idx in range(1, config.run.csv_direct_poll_max_attempts + 1):
                    response = context.request.get(
                        dl_url,
                        timeout=config.run.csv_poll_request_timeout_ms,
                        fail_on_status_code=False,
                    )
                    status = int(response.status)
                    content_type = (response.headers.get("content-type") or "").lower()
                    server = response.headers.get("server")

                    if status == 200 and "text/csv" in content_type:
                        csv_bytes = response.body()
                        if first_csv_504_monotonic is not None:
                            recovery_latency_ms = int(
                                (time.monotonic() - first_csv_504_monotonic) * 1000
                            )
                            recovery_metrics["csv_recovery_count"] += 1
                            recovery_metrics["csv_recovery_latency_ms_total"] += recovery_latency_ms
                            if poll_idx == 2:
                                recovery_metrics["second_csv_200_count"] += 1
                            logging.info(
                                "WTISEN metric csv recovery observed: poll=%s latency_ms=%s csv_recovery_count=%s second_csv_200_count=%s",
                                poll_idx,
                                recovery_latency_ms,
                                recovery_metrics["csv_recovery_count"],
                                recovery_metrics["second_csv_200_count"],
                            )
                        logging.info(
                            "Batch %s attempt %s CSV direct request succeeded on poll %s/%s (%s bytes).",
                            batch_label,
                            attempt,
                            poll_idx,
                            config.run.csv_direct_poll_max_attempts,
                            len(csv_bytes),
                        )
                        return csv_bytes

                    body_snippet = format_response_body_snippet(response, max_chars=800)
                    if status in transient_statuses:
                        if status == 504 and poll_idx == 1 and first_csv_504_monotonic is None:
                            first_csv_504_monotonic = time.monotonic()
                            recovery_metrics["first_csv_504_count"] += 1
                            logging.info(
                                "WTISEN metric first_csv_504_count incremented: %s",
                                recovery_metrics["first_csv_504_count"],
                            )
                        logging.warning(
                            "Batch %s attempt %s CSV poll %s/%s transient status=%s server=%r content-type=%r body_snippet=%r",
                            batch_label,
                            attempt,
                            poll_idx,
                            config.run.csv_direct_poll_max_attempts,
                            status,
                            server,
                            content_type,
                            body_snippet,
                        )
                        if config.run.csv_poll_warmup_on_first_transient and not warmup_used:
                            warmup_used = True
                            run_warmup_request(
                                dl_url=dl_url,
                                batch_label=batch_label,
                                attempt=attempt,
                                timeout_ms=config.run.csv_poll_request_timeout_ms,
                            )
                        if poll_idx < config.run.csv_direct_poll_max_attempts:
                            backoff_ms = config.run.csv_direct_poll_backoff_ms[
                                min(poll_idx - 1, len(config.run.csv_direct_poll_backoff_ms) - 1)
                            ]
                            page.wait_for_timeout(backoff_ms)
                            continue
                        return None

                    logging.warning(
                        "Batch %s attempt %s CSV poll %s/%s non-transient status=%s server=%r content-type=%r body_snippet=%r",
                        batch_label,
                        attempt,
                        poll_idx,
                        config.run.csv_direct_poll_max_attempts,
                        status,
                        server,
                        content_type,
                        body_snippet,
                    )
                    return None

                return None

            def probe_csv_failure(dl_url: str, batch_label: str, attempt: int, reason: str) -> None:
                try:
                    response = context.request.get(
                        dl_url,
                        timeout=config.run.csv_failure_probe_timeout_ms,
                        fail_on_status_code=False,
                    )
                    body_snippet = format_response_body_snippet(response, max_chars=800)
                    logging.warning(
                        "Batch %s attempt %s CSV failure probe (%s): status=%s server=%r content-type=%r body_snippet=%r",
                        batch_label,
                        attempt,
                        reason,
                        response.status,
                        response.headers.get("server"),
                        response.headers.get("content-type"),
                        body_snippet,
                    )
                except Exception as err:
                    logging.warning(
                        "Batch %s attempt %s CSV failure probe (%s) did not complete: %s: %s",
                        batch_label,
                        attempt,
                        reason,
                        type(err).__name__,
                        err,
                    )

            def cleanup_session() -> None:
                if page is None or context is None:
                    return
                try:
                    page.goto(
                        f"{config.source.url.rstrip('/')}/_layouts/15/SignOut.aspx",
                        wait_until="domcontentloaded",
                        timeout=config.run.post_login_timeout_ms,
                    )
                    logging.info("WTISEN sign-out page opened.")
                except Exception:
                    logging.warning("WTISEN_SESSION_CLEANUP category=SIGNOUT_UNSUCCESSFUL")
                    logging.debug("Sign-out failure state url=%s title=%r", sanitize_url(page.url), page_title())
                try:
                    context.clear_cookies()
                    logging.info("WTISEN browser cookies cleared.")
                except Exception:
                    logging.warning("WTISEN_SESSION_CLEANUP category=COOKIE_CLEAR_UNSUCCESSFUL")

            def establish_authenticated_session(stage: str) -> None:
                page.goto(home_url, wait_until="domcontentloaded", timeout=config.run.report_viewer_timeout_ms)
                log_page_state_debug(f"{stage} initial navigation")
                if is_visible(email_input, timeout=5000):
                    logging.info("%s login form detected; running credential flow", stage)
                    email_input.fill(username)
                    email_input.press("Enter")
                    log_page_state_debug(f"{stage} submitted username")

                    identity_error = read_identity_error()
                    if identity_error:
                        raise_auth_error(AuthCategory.USERNAME_REJECTED, detail=identity_error)

                    try:
                        passwd_input.wait_for(state="visible", timeout=config.run.login_timeout_ms)
                    except PlaywrightTimeoutError as exc:
                        identity_error = read_identity_error()
                        log_failure_page_state_debug(f"{stage} password prompt timeout")
                        raise_auth_error(
                            AuthCategory.PASSWORD_PROMPT_TIMEOUT,
                            detail=identity_error,
                            cause=exc,
                        )

                    passwd_input.fill(password)
                    passwd_input.press("Enter")
                    log_page_state_debug(f"{stage} submitted password")
                    identity_error = read_identity_error()
                    if identity_error:
                        raise_auth_error(AuthCategory.PASSWORD_REJECTED, detail=identity_error)

                    unknown_observations = 0
                    for _ in range(4):
                        if login_host not in page.url.lower():
                            break

                        identity_error = read_identity_error()
                        if identity_error:
                            raise_auth_error(AuthCategory.SIGNIN_REJECTED, detail=identity_error)

                        clicked = click_button([r"^no$", r"not now", r"skip"])
                        if clicked:
                            logging.info("Handled Entra prompt using '%s'.", clicked)
                            unknown_observations = 0
                            page.wait_for_timeout(900)
                            continue

                        clicked = click_button([r"^continue$", r"^yes$", r"^ok$"])
                        if clicked:
                            logging.info("Handled Entra prompt using '%s'.", clicked)
                            unknown_observations = 0
                            page.wait_for_timeout(900)
                            continue

                        unknown_observations += 1
                        if unknown_observations >= 3:
                            log_failure_page_state_debug("Unrecognized Entra prompt")
                            raise_auth_error(
                                AuthCategory.MFA_PROMPT_UNRECOGNIZED,
                                detail=f"url={sanitize_url(page.url)}; title={page_title()!r}",
                            )
                        page.wait_for_timeout(900)

                page.goto(home_url, wait_until="domcontentloaded", timeout=config.run.report_viewer_timeout_ms)
                try:
                    page.wait_for_url("**/home.aspx*", timeout=config.run.post_login_timeout_ms)
                except PlaywrightTimeoutError as exc:
                    identity_error = read_identity_error()
                    log_failure_page_state_debug(f"{stage} landing verification timeout")
                    raise_auth_error(
                        AuthCategory.POST_LOGIN_NAVIGATION_TIMEOUT,
                        detail=(
                            f"url={sanitize_url(page.url)}; "
                            f"title={page_title()!r}; "
                            f"identity_error={identity_error or '<none>'}"
                        ),
                        cause=exc,
                    )
                log_page_state_debug(f"{stage} landing verification")
                if is_visible(email_input, timeout=2500):
                    raise_auth_error(AuthCategory.STILL_ON_LOGIN_FORM)
                page.goto(viewer_url, wait_until="domcontentloaded", timeout=config.run.report_viewer_timeout_ms)
                log_page_state_debug(f"{stage} report viewer")
                page.wait_for_timeout(2000)

            def download_once(dl_url: str, batch_label: str) -> bytes:
                for attempt in range(1, config.run.download_retries + 1):
                    try:
                        csv_bytes = fetch_csv_with_polling(
                            dl_url=dl_url,
                            batch_label=batch_label,
                            attempt=attempt,
                        )
                        if csv_bytes is not None:
                            logging.info(
                                "Batch %s attempt %s CSV download succeeded (%s bytes).",
                                batch_label,
                                attempt,
                                len(csv_bytes),
                            )
                            return csv_bytes
                        probe_csv_failure(
                            dl_url=dl_url,
                            batch_label=batch_label,
                            attempt=attempt,
                            reason="CSV_POLL_EXHAUSTED",
                        )
                        raise RuntimeError(
                            f"WTISEN_DOWNLOAD_FAILURE category=CSV_POLL_EXHAUSTED batch={batch_label}"
                        )
                    except PlaywrightTimeoutError as exc:
                        log_failure_page_state_debug(
                            f"Batch {batch_label} retry download timeout attempt {attempt}"
                        )
                        if attempt >= config.run.download_retries:
                            raise RuntimeError("WTISEN_DOWNLOAD_FAILURE category=DOWNLOAD_TIMEOUT") from exc
                    except RuntimeError:
                        log_failure_page_state_debug(
                            f"Batch {batch_label} retry download failure attempt {attempt}"
                        )
                        if attempt >= config.run.download_retries:
                            raise
                    except Exception as exc:
                        log_failure_page_state_debug(
                            f"Batch {batch_label} retry download failure attempt {attempt}"
                        )
                        if attempt >= config.run.download_retries:
                            raise RuntimeError(
                                f"WTISEN_DOWNLOAD_FAILURE category=DOWNLOAD_FAILURE detail={sanitize_text(str(exc))}"
                            ) from exc
                    cleanup_session()
                    establish_authenticated_session(f"Batch {batch_label} retry bootstrap attempt {attempt + 1}")
                raise RuntimeError("WTISEN_DOWNLOAD_FAILURE category=DOWNLOAD_FAILURE")

            establish_authenticated_session("Initial session bootstrap")

            for w_start, w_end in windows:
                query = {
                    "rc:ItemPath": "Tablix4",
                    "prmPHU": config.source.phu_code,
                    "prmSelDate": "0",
                    "prmEnddate": f"{w_end.month}/{w_end.day}/{w_end.year} 23:59:59",
                    "prmStartdate": f"{w_start.month}/{w_start.day}/{w_start.year} 00:00:00",
                    "prmDuplicates": "0",
                    "prmrender:isnull": "True",
                    "rs:ParameterLanguage": "",
                    "rs:Command": "Render",
                    "rs:Format": "CSV",
                }
                dl_url = (
                    f"{config.source.url}/_vti_bin/ReportServer?{config.source.url}/RSReports/{config.source.report_id}&"
                    f"{urlencode(query)}"
                )

                batch_label = f"{w_start}..{w_end}"
                data = download_once(dl_url=dl_url, batch_label=batch_label)

                rows = count_data_rows(data)
                if rows == 0:
                    continue

                fname = f"{run_timestamp}_{w_start.strftime('%Y%m%d')}_{w_end.strftime('%Y%m%d')}_{safe_prefix}.csv"
                storage.write_bytes(config.storage.landing, fname, data)
                files_written += 1
                total_rows += rows
        finally:
            if page is not None and context is not None:
                try:
                    page.goto(
                        f"{config.source.url.rstrip('/')}/_layouts/15/SignOut.aspx",
                        wait_until="domcontentloaded",
                        timeout=config.run.post_login_timeout_ms,
                    )
                except Exception:
                    logging.warning("WTISEN_SESSION_CLEANUP category=SIGNOUT_UNSUCCESSFUL")
                try:
                    context.clear_cookies()
                except Exception:
                    logging.warning("WTISEN_SESSION_CLEANUP category=COOKIE_CLEAR_UNSUCCESSFUL")
            browser.close()

    avg_recovery_latency_ms = 0
    if recovery_metrics["csv_recovery_count"] > 0:
        avg_recovery_latency_ms = int(
            recovery_metrics["csv_recovery_latency_ms_total"] / recovery_metrics["csv_recovery_count"]
        )
    logging.info(
        "WTISEN recovery metrics: first_csv_504_count=%s html_recovery_200_count=%s "
        "second_csv_200_count=%s csv_recovery_count=%s "
        "csv_recovery_latency_ms_total=%s csv_recovery_latency_ms_avg=%s",
        recovery_metrics["first_csv_504_count"],
        recovery_metrics["html_recovery_200_count"],
        recovery_metrics["second_csv_200_count"],
        recovery_metrics["csv_recovery_count"],
        recovery_metrics["csv_recovery_latency_ms_total"],
        avg_recovery_latency_ms,
    )

    if files_written == 0:
        return 0
    return total_rows


def transform_wtisen(
    config: WtisenConfig,
    storage: LocalFilesystemStorage,
    landing_schema_path: Path,
    processed_schema_path: Path,
) -> int:
    return transform_wtisen_with_stats(
        config,
        storage,
        landing_schema_path,
        processed_schema_path,
    ).records


def transform_wtisen_with_stats(
    config: WtisenConfig,
    storage: LocalFilesystemStorage,
    landing_schema_path: Path,
    processed_schema_path: Path,
) -> StageResult:
    with system.use_context(trusted=True):
        landing_schema = Schema.from_descriptor(json.loads(landing_schema_path.read_text()))
        processed_schema = Schema.from_descriptor(json.loads(processed_schema_path.read_text()))

    landing_fields = [field.name for field in landing_schema.fields]
    landing_missing_values = landing_schema.to_descriptor().get("missingValues") or []
    schema_fields = [field.name for field in processed_schema.fields]
    schema_types = {field.name: field.type for field in processed_schema.fields}
    string_columns = [name for name, typ in schema_types.items() if typ == "string"]

    source_aliases = {
        "BARCODE": "barcode",
        "DATE_COLLECTED": "date_collected",
        "DATE_RECEIVED": "date_received",
        "DATE_RELEASED": "date_released",
        "DATE_REPORTED": "date_reported",
        "LABORATORY": "laboratory",
        "PHONE": "phone",
        "ALT_PHONE": "alt_phone",
        "FIRST_NAME": "first_name",
        "LAST_NAME": "last_name",
        "ADDRESS": "address",
        "LOT_NUM": "lot_num",
        "CONCESSION": "concession",
        "CITY": "city",
        "MUNICIPALITY": "municipality",
        "COUNTY": "county",
        "EMERGENCY_LOC_NO": "emergency_loc_no",
        "POSTAL": "postal",
        "ENTRY": "entry",
        "FORMATTED_ENTRY": "formatted_entry",
        "TOTAL_COLIFORM": "total_coliform",
        "E_COLI": "e_coli",
        "REQ_LEGIBLE": "req_legible",
    }

    def normalize_column_name(raw: str) -> str:
        col = raw.strip().upper()
        col = re.sub(r"^SRC_", "", col)
        col = re.sub(r"^SUB_", "", col)
        col = re.sub(r"[^A-Z0-9]+", "_", col)
        col = re.sub(r"_+", "_", col).strip("_")
        if col.endswith("2"):
            col = col[:-1]
        return col

    def parse_mixed_datetime(value):
        if value is None:
            return None
        text = str(value).strip()
        if text == "" or text.lower() in {"nan", "none", "nat", "<na>"}:
            return None
        for fmt in [
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ]:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def normalize_bool(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
        return None

    def postalcode_cleaner(value):
        if value is None or not isinstance(value, str):
            return None
        cleaned = re.sub(r"[\W_]", "", value).upper()
        return cleaned[:6] if cleaned else None

    def cast_to_schema(df: pl.DataFrame) -> pl.DataFrame:
        cast_exprs = []
        for field in processed_schema.fields:
            name = field.name
            if name not in df.columns:
                continue
            if field.type == "string":
                cast_exprs.append(pl.col(name).cast(pl.Utf8, strict=False).alias(name))
            elif field.type == "integer":
                cast_exprs.append(pl.col(name).cast(pl.Int64, strict=False).alias(name))
            elif field.type == "number":
                cast_exprs.append(pl.col(name).cast(pl.Float64, strict=False).alias(name))
            elif field.type == "boolean":
                cast_exprs.append(pl.col(name).cast(pl.Boolean, strict=False).alias(name))
            elif field.type == "datetime":
                cast_exprs.append(pl.col(name).cast(pl.Datetime, strict=False).alias(name))
            elif field.type == "date":
                cast_exprs.append(pl.col(name).cast(pl.Date, strict=False).alias(name))
        return df.with_columns(cast_exprs) if cast_exprs else df

    landing_files = storage.list_files(config.storage.landing, config.transform.file_pattern)
    landing_files.sort(key=lambda p: _file_order_key(p.name))
    if not landing_files:
        logging.info("No landing files found for transform")
        return StageResult(records=0, files_total=0, files_loaded=0, files_skipped=0)

    version = "v2.0"
    total_records = 0
    files_loaded = 0
    files_skipped = 0

    for file_path in landing_files:
        try:
            raw_df = None
            last_error = None
            data = file_path.read_bytes()
            for skip_rows in (4, 3, 0):
                try:
                    candidate = pl.read_csv(
                        io.BytesIO(data),
                        null_values=landing_missing_values,
                        infer_schema_length=0,
                        try_parse_dates=False,
                        skip_rows=skip_rows,
                    )
                    cols = [normalize_column_name(c) for c in candidate.columns]
                    if {"BARCODE", "DATE_COLLECTED"}.issubset(set(cols)):
                        raw_df = candidate
                        break
                except Exception as exc:
                    last_error = exc
            if raw_df is None:
                raise ValueError(f"Could not parse CSV {file_path.name}: {last_error}")

            if raw_df.height > 0:
                checks = [
                    pl.col(c).cast(pl.Utf8, strict=False).str.strip_chars().is_not_null()
                    & pl.col(c).cast(pl.Utf8, strict=False).str.strip_chars().ne("")
                    for c in raw_df.columns
                ]
                raw_df = raw_df.filter(pl.any_horizontal(checks))

            if raw_df.height == 0:
                files_skipped += 1
                continue

            norm_map = {c: normalize_column_name(c) for c in raw_df.columns}
            df = raw_df.rename(norm_map)

            landing_missing = [c for c in landing_fields if c not in df.columns]
            if landing_missing:
                df_for_landing = df.with_columns([pl.lit(None).alias(c) for c in landing_missing])
            else:
                df_for_landing = df
            landing_df = df_for_landing.select(landing_fields)
            with system.use_context(trusted=True):
                report = Resource(landing_df.to_dicts(), schema=landing_schema).validate()
            if not report.valid:
                raise ValueError(f"Landing schema validation failed for {file_path.name}")

            rename_map = {
                c: source_aliases[c]
                for c in df.columns
                if c in source_aliases and source_aliases[c] in schema_fields
            }
            if rename_map:
                df = df.rename(rename_map)

            missing_processed = [c for c in schema_fields if c not in df.columns]
            if missing_processed:
                df = df.with_columns([pl.lit(None).alias(c) for c in missing_processed])
            df = df.select(schema_fields)

            if "postal" in df.columns:
                df = df.with_columns(
                    pl.col("postal").map_elements(postalcode_cleaner, return_dtype=pl.Utf8).alias("postal")
                )
            if "entry" in df.columns:
                df = df.with_columns(pl.col("entry").cast(pl.Int64, strict=False).alias("entry"))
            if "req_legible" in df.columns:
                df = df.with_columns(
                    pl.col("req_legible").map_elements(normalize_bool, return_dtype=pl.Boolean).alias("req_legible")
                )

            for col in ["date_collected", "date_received", "date_released", "date_reported"]:
                if col in df.columns:
                    df = df.with_columns(
                        pl.col(col).map_elements(parse_mixed_datetime, return_dtype=pl.Datetime).alias(col)
                    )

            null_like_tokens = ["", "nan", "none", "nat", "<na>"]
            if string_columns:
                df = df.with_columns(
                    [
                        pl.when(pl.col(c).is_null())
                        .then(None)
                        .otherwise(pl.col(c).cast(pl.Utf8, strict=False).str.strip_chars())
                        .alias(c)
                        for c in string_columns
                        if c in df.columns
                    ]
                )
                df = df.with_columns(
                    [
                        pl.when(pl.col(c).is_null())
                        .then(None)
                        .when(pl.col(c).str.to_lowercase().is_in(null_like_tokens))
                        .then(None)
                        .otherwise(pl.col(c))
                        .alias(c)
                        for c in string_columns
                        if c in df.columns
                    ]
                )

            df = cast_to_schema(df)
            with system.use_context(trusted=True):
                report = Resource(df.to_dicts(), schema=processed_schema).validate()
            if not report.valid:
                raise ValueError(f"Processed schema validation failed for {file_path.name}")

            output_name = f"{file_path.stem}_{version}.parquet"
            out = storage.resolve(config.storage.processed) / output_name
            out.parent.mkdir(parents=True, exist_ok=True)
            df.write_parquet(out)
            total_records += df.height
            files_loaded += 1

            if config.run.archive_enabled:
                storage.move_file(file_path, config.storage.archive_landing)
        except Exception as exc:
            files_skipped += 1
            logging.error("Failed to transform %s: %s", file_path.name, exc, exc_info=True)

    if files_loaded == 0 and files_skipped > 0:
        raise ValueError("All WTISEN landing files failed transformation")
    return StageResult(
        records=total_records,
        files_total=len(landing_files),
        files_loaded=files_loaded,
        files_skipped=files_skipped,
    )


def load_wtisen(config: WtisenConfig, storage: LocalFilesystemStorage, processed_schema_path: Path) -> int:
    return load_wtisen_with_stats(config, storage, processed_schema_path).records


def load_wtisen_with_stats(
    config: WtisenConfig,
    storage: LocalFilesystemStorage,
    processed_schema_path: Path,
) -> StageResult:
    with system.use_context(trusted=True):
        processed_schema = Schema.from_descriptor(json.loads(processed_schema_path.read_text()))

    def cast_to_schema(df: pl.DataFrame) -> pl.DataFrame:
        cast_exprs = []
        for field in processed_schema.fields:
            name = field.name
            if name not in df.columns:
                continue
            if field.type == "string":
                cast_exprs.append(pl.col(name).cast(pl.Utf8, strict=False).alias(name))
            elif field.type == "integer":
                cast_exprs.append(pl.col(name).cast(pl.Int64, strict=False).alias(name))
            elif field.type == "number":
                cast_exprs.append(pl.col(name).cast(pl.Float64, strict=False).alias(name))
            elif field.type == "boolean":
                cast_exprs.append(pl.col(name).cast(pl.Boolean, strict=False).alias(name))
            elif field.type == "datetime":
                cast_exprs.append(pl.col(name).cast(pl.Datetime, strict=False).alias(name))
            elif field.type == "date":
                cast_exprs.append(pl.col(name).cast(pl.Date, strict=False).alias(name))
        return df.with_columns(cast_exprs) if cast_exprs else df

    files = storage.list_files(config.storage.processed, config.load.file_pattern)
    files.sort(key=lambda p: _file_order_key(p.name))
    if not files:
        logging.info("No processed files found for load")
        return StageResult(records=0, files_total=0, files_loaded=0, files_skipped=0)

    curated_path = storage.resolve(config.storage.curated)
    curated_path.parent.mkdir(parents=True, exist_ok=True)
    if curated_path.exists():
        target_df = pl.read_parquet(curated_path)
    else:
        target_df = pl.DataFrame(schema={f.name: pl.Utf8 for f in processed_schema.fields})

    total_records = 0
    files_loaded = 0
    files_skipped = 0

    for file_path in files:
        try:
            src = cast_to_schema(pl.read_parquet(file_path))
            missing_columns = [f.name for f in processed_schema.fields if f.name not in src.columns]
            if missing_columns:
                raise ValueError(f"Missing required columns: {missing_columns}")
            with system.use_context(trusted=True):
                report = Resource(src.to_dicts(), schema=processed_schema).validate()
            if not report.valid:
                raise ValueError(f"Schema validation failed for {file_path.name}")

            if "barcode" in src.columns:
                src = src.filter(pl.col("barcode").is_not_null())
            if src.height == 0:
                files_skipped += 1
                continue

            missing_keys = [k for k in config.load.merge_keys if k not in src.columns]
            if missing_keys:
                raise ValueError(f"Merge keys missing from {file_path.name}: {missing_keys}")

            src = src.unique(subset=config.load.merge_keys, keep="last")
            combined = pl.concat([target_df, src], how="diagonal_relaxed")
            target_df = combined.unique(subset=config.load.merge_keys, keep="last")

            total_records += src.height
            files_loaded += 1
            if config.run.archive_enabled:
                storage.move_file(file_path, config.storage.archive_processed)
        except Exception as exc:
            files_skipped += 1
            logging.error("Failed to load %s: %s", file_path.name, exc, exc_info=True)

    if files_loaded == 0 and files_skipped > 0:
        raise ValueError("All WTISEN processed files failed load")

    target_df.write_parquet(curated_path)
    return StageResult(
        records=total_records,
        files_total=len(files),
        files_loaded=files_loaded,
        files_skipped=files_skipped,
    )
