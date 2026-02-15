#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import tomllib


class CommandError(RuntimeError):
    def __init__(self, command: list[str], code: int, stderr: str) -> None:
        super().__init__(f"Command failed ({code}): {' '.join(command)}\n{stderr.strip()}")
        self.command = command
        self.code = code
        self.stderr = stderr


@dataclass(slots=True)
class SprintItemResult:
    item_id: str
    title: str
    issue_number: int
    issue_url: str
    issue_state: str
    pr_number: int | None
    pr_url: str | None
    pr_state: str | None


def run_command(command: list[str], *, input_text: str | None = None) -> str:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
    )
    if completed.returncode != 0:
        raise CommandError(command, completed.returncode, completed.stderr)
    return completed.stdout


def gh_api(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    command = ["gh", "api", path, "--method", method]
    command_suffix = ["--input", "-"] if payload is not None else []
    payload_text = json.dumps(payload) if payload is not None else None
    raw = run_command(command + command_suffix, input_text=payload_text)
    return json.loads(raw) if raw.strip() else {}


def infer_repo() -> tuple[str, str]:
    data = gh_api("repos/:owner/:repo")
    return data["owner"]["login"], data["name"]


def ensure_milestone(owner: str, repo: str, title: str, description: str | None) -> int:
    existing = gh_api(f"repos/{owner}/{repo}/milestones?state=all&per_page=100")
    for milestone in existing:
        if milestone["title"] == title:
            return int(milestone["number"])

    payload: dict[str, Any] = {"title": title, "state": "open"}
    if description:
        payload["description"] = description
    created = gh_api(f"repos/{owner}/{repo}/milestones", method="POST", payload=payload)
    return int(created["number"])


def label_spec(name: str) -> tuple[str, str]:
    lowered = name.lower()
    if lowered.startswith("sprint:"):
        return "0e8a16", "Sprint scope tracking"
    if lowered.startswith("type:"):
        return "1d76db", "Work type"
    if lowered.startswith("area:"):
        return "5319e7", "Functional area"
    if lowered.startswith("priority:"):
        return "d93f0b", "Delivery priority"
    if lowered in {"tech-debt", "techdebt"}:
        return "b60205", "Technical debt"
    return "6e7781", "Planning automation"


def ensure_label(owner: str, repo: str, label_name: str) -> None:
    encoded = quote(label_name, safe="")
    try:
        gh_api(f"repos/{owner}/{repo}/labels/{encoded}")
        return
    except CommandError as exc:
        if "404" not in exc.stderr:
            raise

    color, description = label_spec(label_name)
    gh_api(
        f"repos/{owner}/{repo}/labels",
        method="POST",
        payload={"name": label_name, "color": color, "description": description},
    )


def find_issue(owner: str, repo: str, item_id: str) -> dict[str, Any] | None:
    query = f"repo:{owner}/{repo} is:issue in:title [{item_id}]"
    encoded_query = quote(query, safe="")
    found = gh_api(f"search/issues?q={encoded_query}&per_page=20")
    for issue in found.get("items", []):
        title = issue.get("title", "")
        if f"[{item_id}]" in title:
            return issue
    return None


def build_issue_body(manifest_sprint: str, item: dict[str, Any]) -> str:
    acceptance = item.get("acceptance", [])
    acceptance_lines = (
        "\n".join(f"- [ ] {line}" for line in acceptance) or "- [ ] Fill acceptance criteria"
    )

    description = item.get("description", "No description provided yet.").strip()
    estimate = str(item.get("estimate", "?")).strip()
    priority = str(item.get("priority", "P2")).strip()
    tech_debt = bool(item.get("tech_debt", False))

    return textwrap.dedent(
        f"""
        <!-- sprint-item-id:{item['id']} -->
        ## Goal
        {description}

        ## Scope
        - Sprint: {manifest_sprint}
        - Priority: {priority}
        - Estimate: {estimate}
        - Tech debt: {'yes' if tech_debt else 'no'}

        ## Acceptance Criteria
        {acceptance_lines}

        ## Notes
        - Keep PR small and linked to this issue.
        - Add CI evidence in PR validation section.
        """
    ).strip()


def upsert_issue(
    *,
    owner: str,
    repo: str,
    sprint: str,
    milestone_number: int,
    item: dict[str, Any],
) -> dict[str, Any]:
    item_id = str(item["id"]).strip()
    item_title = str(item["title"]).strip()
    if not item_id or not re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
        raise ValueError(f"Invalid item id '{item_id}'. Use letters/numbers/_/- only.")

    issue_title = f"[{sprint}][{item_id}] {item_title}"
    body = build_issue_body(sprint, item)

    labels = [str(label).strip() for label in item.get("labels", []) if str(label).strip()]
    labels.append(f"sprint:{sprint.lower().replace(' ', '-')}")
    labels = sorted(set(labels))
    for label in labels:
        ensure_label(owner, repo, label)

    issue = find_issue(owner, repo, item_id)
    assignees = [
        str(value).strip() for value in item.get("assignees", []) if str(value).strip()
    ]

    if issue is None:
        payload: dict[str, Any] = {
            "title": issue_title,
            "body": body,
            "milestone": milestone_number,
            "labels": labels,
        }
        if assignees:
            payload["assignees"] = assignees
        return gh_api(f"repos/{owner}/{repo}/issues", method="POST", payload=payload)

    issue_number = int(issue["number"])
    merged_labels = sorted({*(label["name"] for label in issue.get("labels", [])), *labels})
    payload = {
        "title": issue_title,
        "body": body,
        "milestone": milestone_number,
        "labels": merged_labels,
    }
    if assignees:
        payload["assignees"] = assignees
    return gh_api(f"repos/{owner}/{repo}/issues/{issue_number}", method="PATCH", payload=payload)


def default_branch_name(sprint: str, item: dict[str, Any]) -> str:
    raw = re.sub(r"[^a-z0-9-]+", "-", str(item["title"]).lower()).strip("-")
    item_id = str(item["id"]).lower()
    sprint_slug = re.sub(r"[^a-z0-9]+", "-", sprint.lower()).strip("-")
    return f"{sprint_slug}/{item_id}-{raw[:40]}".rstrip("-")


def ensure_remote_branch(owner: str, repo: str, branch: str, base_branch: str) -> None:
    encoded_base = quote(base_branch, safe="")
    base_ref = gh_api(f"repos/{owner}/{repo}/git/ref/heads/{encoded_base}")
    base_sha = base_ref["object"]["sha"]

    payload = {"ref": f"refs/heads/{branch}", "sha": base_sha}
    try:
        gh_api(f"repos/{owner}/{repo}/git/refs", method="POST", payload=payload)
    except CommandError as exc:
        if "Reference already exists" not in exc.stderr and "422" not in exc.stderr:
            raise


def find_pr_by_branch(owner: str, repo: str, branch: str) -> dict[str, Any] | None:
    params = urlencode({"state": "all", "head": f"{owner}:{branch}", "per_page": 20})
    prs = gh_api(f"repos/{owner}/{repo}/pulls?{params}")
    return prs[0] if prs else None


def ensure_draft_pr(
    *,
    owner: str,
    repo: str,
    sprint: str,
    item: dict[str, Any],
    issue_number: int,
    base_branch: str,
) -> dict[str, Any]:
    branch = str(item.get("branch") or default_branch_name(sprint, item)).strip()
    if not branch:
        raise ValueError(f"Branch name is empty for item {item['id']}")

    ensure_remote_branch(owner, repo, branch, base_branch)

    existing = find_pr_by_branch(owner, repo, branch)
    if existing is not None:
        return existing

    pr_title = f"[{sprint}][{item['id']}] {item['title']}"
    pr_body = textwrap.dedent(
        f"""
        ## Summary
        - Planned sprint task scaffold PR.
        - Implementation should be delivered in focused commits.

        ## Linked Issue
        Closes #{issue_number}

        ## Validation
        - [ ] python -m ruff check app tests
        - [ ] python -m pytest -q tests
        - [ ] RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=... python -m pytest -q tests/integration
        """
    ).strip()

    return gh_api(
        f"repos/{owner}/{repo}/pulls",
        method="POST",
        payload={
            "title": pr_title,
            "head": branch,
            "base": base_branch,
            "body": pr_body,
            "draft": True,
        },
    )


def write_status_file(path: Path, sprint: str, results: list[SprintItemResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Planning Status",
        "",
        f"Last sync: {generated_at}",
        f"Active sprint: {sprint}",
        "",
        "| Item | Title | Issue | PR |",
        "|---|---|---|---|",
    ]

    for result in results:
        issue = f"[#{result.issue_number}]({result.issue_url}) ({result.issue_state})"
        pr = (
            f"[#{result.pr_number}]({result.pr_url}) ({result.pr_state})"
            if result.pr_number is not None and result.pr_url is not None and result.pr_state is not None
            else "-"
        )
        lines.append(f"| {result.item_id} | {result.title} | {issue} | {pr} |")

    lines.extend(
        [
            "",
            "## Recovery Checklist",
            "",
            "- Keep this file updated at each planning sync.",
            "- Link every implementation PR to an issue with `Closes #<id>`.",
            "- Keep sprint labels (`sprint:*`) on every active PR.",
        ]
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync sprint manifest to GitHub issues and draft PRs")
    parser.add_argument("--manifest", required=True, help="Path to sprint manifest TOML file")
    parser.add_argument("--owner", help="GitHub owner/org (defaults to current repo)")
    parser.add_argument("--repo", help="GitHub repository name (defaults to current repo)")
    parser.add_argument("--base-branch", help="Base branch override")
    parser.add_argument(
        "--create-draft-prs",
        action="store_true",
        help="Create/update draft PR scaffolds for items with create_draft_pr=true",
    )
    parser.add_argument(
        "--skip-status",
        action="store_true",
        help="Skip writing status markdown file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).resolve()
    manifest = load_manifest(manifest_path)

    owner = args.owner
    repo = args.repo
    if not owner or not repo:
        inferred_owner, inferred_repo = infer_repo()
        owner = owner or inferred_owner
        repo = repo or inferred_repo

    sprint = str(manifest.get("sprint", "Sprint Unknown")).strip()
    milestone_title = str(manifest.get("milestone", sprint)).strip()
    milestone_description = manifest.get("milestone_description")
    base_branch = str(args.base_branch or manifest.get("base_branch", "main")).strip()

    items = manifest.get("items", [])
    if not isinstance(items, list) or not items:
        raise ValueError("Manifest must contain at least one [[items]] section")

    milestone_number = ensure_milestone(owner, repo, milestone_title, milestone_description)

    results: list[SprintItemResult] = []
    for item in items:
        issue = upsert_issue(
            owner=owner,
            repo=repo,
            sprint=sprint,
            milestone_number=milestone_number,
            item=item,
        )

        pr_number: int | None = None
        pr_url: str | None = None
        pr_state: str | None = None
        should_create_pr = bool(item.get("create_draft_pr", True)) and args.create_draft_prs
        if should_create_pr:
            pr = ensure_draft_pr(
                owner=owner,
                repo=repo,
                sprint=sprint,
                item=item,
                issue_number=int(issue["number"]),
                base_branch=base_branch,
            )
            pr_number = int(pr["number"])
            pr_url = str(pr["html_url"])
            pr_state = str(pr["state"])

        results.append(
            SprintItemResult(
                item_id=str(item["id"]),
                title=str(item["title"]),
                issue_number=int(issue["number"]),
                issue_url=str(issue["html_url"]),
                issue_state=str(issue["state"]),
                pr_number=pr_number,
                pr_url=pr_url,
                pr_state=pr_state,
            )
        )

    status_path_raw = str(manifest.get("status_file", "planning/STATUS.md")).strip()
    status_path = Path(status_path_raw)
    if not status_path.is_absolute():
        status_path = (Path.cwd() / status_path).resolve()
    if not args.skip_status:
        write_status_file(status_path, sprint, results)

    print(f"Synced {len(results)} sprint items for {owner}/{repo} ({sprint}).")
    for result in results:
        pr_info = f" | PR #{result.pr_number}" if result.pr_number else ""
        print(f"- {result.item_id}: issue #{result.issue_number}{pr_info}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CommandError, ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
