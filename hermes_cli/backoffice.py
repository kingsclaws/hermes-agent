"""Backoffice issue inbox for Lex-Hermes maintenance reports."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_ROOT = Path("/workingfile")


@dataclass(frozen=True)
class BackofficeReport:
    report_path: Path
    issue_path: Path
    issue: dict

    @property
    def issue_id(self) -> str:
        return str(self.issue.get("id") or self.report_path.stem)

    @property
    def status(self) -> str:
        return str(self.issue.get("status") or "open")

    @property
    def severity(self) -> str:
        return str(self.issue.get("severity") or "")

    @property
    def category(self) -> str:
        return str(self.issue.get("category") or "")

    @property
    def summary(self) -> str:
        return str(self.issue.get("summary") or "").strip()

    @property
    def sort_key(self) -> tuple[float, str]:
        try:
            mtime = self.report_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (mtime, str(self.report_path))


def _candidate_report_paths(root: Path) -> Iterable[Path]:
    global_dir = root / ".lex-hermes-backoffice" / "issues"
    if global_dir.is_dir():
        yield from global_dir.glob("*.REPORT.md")
    if root.is_dir():
        yield from root.glob("*/.hermes-project/backoffice/issues/*.REPORT.md")


def _issue_path_for_report(report_path: Path) -> Path:
    name = report_path.name
    if name.endswith(".REPORT.md"):
        return report_path.with_name(name.removesuffix(".REPORT.md") + ".json")
    return report_path.with_suffix(".json")


def _load_issue(report_path: Path) -> dict:
    issue_path = _issue_path_for_report(report_path)
    if issue_path.exists():
        try:
            return json.loads(issue_path.read_text(encoding="utf-8"))
        except Exception:
            return {"id": report_path.stem, "status": "unreadable-json"}
    return {"id": report_path.stem, "status": "missing-json"}


def scan_reports(root: Path = DEFAULT_ROOT, *, statuses: Sequence[str] | None = None) -> list[BackofficeReport]:
    wanted = {s.lower() for s in statuses or [] if s}
    reports: list[BackofficeReport] = []
    for report_path in _candidate_report_paths(root):
        issue_path = _issue_path_for_report(report_path)
        issue = _load_issue(report_path)
        item = BackofficeReport(report_path=report_path, issue_path=issue_path, issue=issue)
        if wanted and item.status.lower() not in wanted:
            continue
        reports.append(item)
    return sorted(reports, key=lambda r: r.sort_key, reverse=True)


def _print_list(reports: Sequence[BackofficeReport], *, limit: int = 20) -> None:
    if not reports:
        print("No backoffice reports found.")
        return
    for item in reports[:limit]:
        print(
            f"{item.issue_id} [{item.status}] {item.severity}/{item.category} "
            f"{item.summary}\n  {item.report_path}"
        )


def _resolve_report(selector: str, reports: Sequence[BackofficeReport]) -> BackofficeReport:
    if selector in {"", "latest"}:
        if not reports:
            raise SystemExit("No backoffice reports found.")
        return reports[0]
    path = Path(selector)
    if path.exists():
        return BackofficeReport(path, _issue_path_for_report(path), _load_issue(path))
    for item in reports:
        if selector == item.issue_id or selector in item.issue_id or selector in str(item.report_path):
            return item
    raise SystemExit(f"Backoffice report not found: {selector}")


def _run_notify(command: str, report: BackofficeReport) -> None:
    env = os.environ.copy()
    env.update(
        {
            "BACKOFFICE_REPORT": str(report.report_path),
            "BACKOFFICE_ISSUE": str(report.issue_path),
            "BACKOFFICE_ID": report.issue_id,
            "BACKOFFICE_SUMMARY": report.summary,
        }
    )
    subprocess.run(command, shell=True, check=False, env=env)


def _cmd_list(args: argparse.Namespace) -> int:
    reports = scan_reports(args.root, statuses=args.status)
    _print_list(reports, limit=args.limit)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    reports = scan_reports(args.root, statuses=args.status)
    item = _resolve_report(args.selector, reports)
    if args.path_only:
        print(item.report_path)
        return 0
    print(item.report_path.read_text(encoding="utf-8"))
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    seen: set[str] = set()
    if not args.include_existing:
        seen = {str(item.report_path) for item in scan_reports(args.root, statuses=args.status)}
    while True:
        reports = scan_reports(args.root, statuses=args.status)
        new_reports = [item for item in reversed(reports) if str(item.report_path) not in seen]
        for item in new_reports:
            seen.add(str(item.report_path))
            print(
                f"[backoffice] {item.issue_id} {item.severity}/{item.category}: {item.summary}\n"
                f"  {item.report_path}",
                flush=True,
            )
            if args.print_report:
                print(item.report_path.read_text(encoding="utf-8"), flush=True)
            if args.notify_command:
                _run_notify(args.notify_command, item)
        if args.once:
            return 0
        time.sleep(args.interval)


def build_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "backoffice",
        help="Read Lex-Hermes backoffice diagnostic reports",
        description="Scan the shared /workingfile inbox for lex-hermes-generated *.REPORT.md diagnostics.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Shared working root to scan (default: /workingfile)",
    )
    parser.add_argument(
        "--status",
        action="append",
        default=["open", "needs_user"],
        help="Issue status to include; repeatable. Default: open and needs_user",
    )
    action = parser.add_subparsers(dest="backoffice_action")

    list_parser = action.add_parser("list", aliases=["ls"], help="List reports")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.set_defaults(backoffice_func=_cmd_list)

    show_parser = action.add_parser("show", help="Show one report")
    show_parser.add_argument("selector", nargs="?", default="latest", help="latest, id fragment, or report path")
    show_parser.add_argument("--path-only", action="store_true", help="Print only the report path")
    show_parser.set_defaults(backoffice_func=_cmd_show)

    watch_parser = action.add_parser("watch", help="Poll for new reports")
    watch_parser.add_argument("--interval", type=float, default=10.0, help="Polling interval in seconds")
    watch_parser.add_argument("--once", action="store_true", help="Scan once and exit")
    watch_parser.add_argument("--include-existing", action="store_true", help="Treat existing reports as new")
    watch_parser.add_argument("--print-report", action="store_true", help="Print the full report when found")
    watch_parser.add_argument(
        "--notify-command",
        help=(
            "Shell command to run for each new report. Receives BACKOFFICE_REPORT, "
            "BACKOFFICE_ISSUE, BACKOFFICE_ID, and BACKOFFICE_SUMMARY env vars."
        ),
    )
    watch_parser.set_defaults(backoffice_func=_cmd_watch)

    parser.set_defaults(backoffice_func=_cmd_list)
    return parser


def backoffice_command(args: argparse.Namespace) -> int:
    func = getattr(args, "backoffice_func", _cmd_list)
    return int(func(args) or 0)
