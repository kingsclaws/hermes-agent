"""Backoffice issue inbox for Lex-Hermes maintenance reports."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_issue(path: Path, issue: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(issue, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_resolution_to_report(report: BackofficeReport, heading: str, lines: Sequence[str]) -> None:
    if not report.report_path.exists():
        return
    body = report.report_path.read_text(encoding="utf-8")
    addition = "\n\n## " + heading + "\n\n" + "\n".join(lines) + "\n"
    report.report_path.write_text(body.rstrip() + addition, encoding="utf-8")


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


def _all_reports_for_resolution(root: Path) -> list[BackofficeReport]:
    return scan_reports(root, statuses=None)


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


def _cmd_claim(args: argparse.Namespace) -> int:
    item = _resolve_report(args.selector, _all_reports_for_resolution(args.root))
    issue = dict(item.issue)
    issue["status"] = "triaged"
    issue["claimed_at"] = _now()
    issue["claimed_by"] = args.by
    if args.note:
        issue["claim_note"] = args.note
    _write_issue(item.issue_path, issue)
    _append_resolution_to_report(
        item,
        "Backoffice Claim",
        [
            f"- Claimed at: `{issue['claimed_at']}`",
            f"- Claimed by: `{args.by}`",
            f"- Note: {args.note or ''}",
        ],
    )
    print(f"Claimed {item.issue_id}: {item.issue_path}")
    return 0


def _cmd_fix(args: argparse.Namespace) -> int:
    if not args.allow_incomplete and (not args.commit or not args.image_digest):
        raise SystemExit("backoffice fix requires --commit and --image-digest unless --allow-incomplete is set")
    item = _resolve_report(args.selector, _all_reports_for_resolution(args.root))
    issue = dict(item.issue)
    issue["status"] = "fixed"
    issue["fixed_at"] = _now()
    issue["fixed_by"] = args.by
    issue["fix_commit"] = args.commit or issue.get("fix_commit", "")
    issue["fixed_image_digest"] = args.image_digest or issue.get("fixed_image_digest", "")
    issue["fix_note"] = args.note or ""
    issue["tests"] = args.test or []
    issue["delivery_standard"] = {
        "commit_required": True,
        "push_required": True,
        "image_build_push_required": True,
        "commit": issue["fix_commit"],
        "image_digest": issue["fixed_image_digest"],
        "tests": issue["tests"],
        "complete": bool(issue["fix_commit"] and issue["fixed_image_digest"]),
    }
    _write_issue(item.issue_path, issue)
    _append_resolution_to_report(
        item,
        "Backoffice Fix",
        [
            f"- Fixed at: `{issue['fixed_at']}`",
            f"- Fixed by: `{args.by}`",
            f"- Commit: `{issue['fix_commit']}`",
            f"- Image digest: `{issue['fixed_image_digest']}`",
            f"- Tests: {', '.join(issue['tests']) if issue['tests'] else ''}",
            f"- Note: {args.note or ''}",
        ],
    )
    print(f"Fixed {item.issue_id}: {item.issue_path}")
    if args.remove_report and item.report_path.exists():
        item.report_path.unlink()
        print(f"Removed report: {item.report_path}")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    item = _resolve_report(args.selector, _all_reports_for_resolution(args.root))
    issue = dict(item.issue)
    issue["status"] = "archived"
    issue["archived_at"] = _now()
    issue["archived_by"] = args.by
    if args.note:
        issue["archive_note"] = args.note
    _write_issue(item.issue_path, issue)
    if args.remove_report and item.report_path.exists():
        item.report_path.unlink()
        print(f"Archived {item.issue_id} and removed report: {item.report_path}")
    else:
        _append_resolution_to_report(
            item,
            "Backoffice Archive",
            [
                f"- Archived at: `{issue['archived_at']}`",
                f"- Archived by: `{args.by}`",
                f"- Note: {args.note or ''}",
            ],
        )
        print(f"Archived {item.issue_id}: {item.issue_path}")
    return 0


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

    claim_parser = action.add_parser("claim", help="Mark a report as triaged/claimed")
    claim_parser.add_argument("selector", nargs="?", default="latest", help="latest, id fragment, or report path")
    claim_parser.add_argument("--by", default=os.environ.get("USER", "maintainer"), help="Maintainer identity")
    claim_parser.add_argument("--note", default="", help="Claim note")
    claim_parser.set_defaults(backoffice_func=_cmd_claim)

    fix_parser = action.add_parser("fix", help="Mark a report fixed with delivery evidence")
    fix_parser.add_argument("selector", nargs="?", default="latest", help="latest, id fragment, or report path")
    fix_parser.add_argument("--by", default=os.environ.get("USER", "maintainer"), help="Maintainer identity")
    fix_parser.add_argument("--commit", help="Fix commit hash pushed to remote")
    fix_parser.add_argument("--image-digest", help="Built and pushed image digest")
    fix_parser.add_argument("--test", action="append", default=[], help="Verification command/result; repeatable")
    fix_parser.add_argument("--note", default="", help="Fix note")
    fix_parser.add_argument("--allow-incomplete", action="store_true", help="Allow fixed status without full delivery evidence")
    fix_parser.add_argument("--remove-report", action="store_true", help="Remove the Markdown report after updating JSON")
    fix_parser.set_defaults(backoffice_func=_cmd_fix)

    archive_parser = action.add_parser("archive", help="Archive a report after it is no longer actionable")
    archive_parser.add_argument("selector", nargs="?", default="latest", help="latest, id fragment, or report path")
    archive_parser.add_argument("--by", default=os.environ.get("USER", "maintainer"), help="Maintainer identity")
    archive_parser.add_argument("--note", default="", help="Archive note")
    archive_parser.add_argument("--remove-report", action="store_true", help="Remove the Markdown report from the active inbox")
    archive_parser.set_defaults(backoffice_func=_cmd_archive)

    parser.set_defaults(backoffice_func=_cmd_list)
    return parser


def backoffice_command(args: argparse.Namespace) -> int:
    func = getattr(args, "backoffice_func", _cmd_list)
    return int(func(args) or 0)
