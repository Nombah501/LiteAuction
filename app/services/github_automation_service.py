from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import settings
from app.db.enums import FeedbackType
from app.db.models import FeedbackItem, User


@dataclass(slots=True)
class GitHubIssueRef:
    number: int
    url: str


@dataclass(slots=True)
class FeedbackIssueDraft:
    title: str
    body: str
    labels: list[str]


class FeedbackIssueClient:
    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> GitHubIssueRef:
        raise NotImplementedError


class GitHubApiIssueClient(FeedbackIssueClient):
    def __init__(self, *, token: str, repo_owner: str, repo_name: str) -> None:
        self._token = token.strip()
        self._repo_owner = repo_owner.strip()
        self._repo_name = repo_name.strip()

    @classmethod
    def from_settings(cls) -> GitHubApiIssueClient:
        return cls(
            token=settings.github_token,
            repo_owner=settings.github_repo_owner,
            repo_name=settings.github_repo_name,
        )

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> GitHubIssueRef:
        if not self._token:
            raise RuntimeError("GITHUB_TOKEN is empty")
        if not self._repo_owner or not self._repo_name:
            raise RuntimeError("GitHub target repository is not configured")

        url = f"https://api.github.com/repos/{self._repo_owner}/{self._repo_name}/issues"
        payload = json.dumps({"title": title, "body": body, "labels": labels}).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "LiteAuctionBotAutomation/1.0",
            "Content-Type": "application/json",
        }
        request = Request(url=url, data=payload, headers=headers, method="POST")

        def _send() -> tuple[int, str]:
            with urlopen(request, timeout=15) as response:  # noqa: S310
                return int(response.status), response.read().decode("utf-8")

        try:
            status, raw_body = await asyncio.to_thread(_send)
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API error: HTTP {exc.code}: {error_body[:400]}") from exc
        except URLError as exc:
            raise RuntimeError(f"GitHub API unavailable: {exc}") from exc

        if status not in {200, 201}:
            raise RuntimeError(f"GitHub API unexpected status: {status}")

        data = json.loads(raw_body)
        issue_number = data.get("number")
        issue_url = data.get("html_url")
        if not isinstance(issue_number, int) or not isinstance(issue_url, str) or not issue_url:
            raise RuntimeError("GitHub API returned malformed issue payload")

        return GitHubIssueRef(number=issue_number, url=issue_url)


def labels_for_feedback_type(feedback_type: FeedbackType) -> list[str]:
    if feedback_type == FeedbackType.BUG:
        return ["bug-approved"]
    return ["suggestion-approved"]


def build_feedback_issue_draft(
    *,
    item: FeedbackItem,
    submitter: User | None,
    moderator: User | None,
) -> FeedbackIssueDraft:
    feedback_type = FeedbackType(item.type)
    type_label = "Баг" if feedback_type == FeedbackType.BUG else "Предложение"
    submitter_label = "-"
    if submitter is not None:
        submitter_label = f"{submitter.tg_user_id}"
        if submitter.username:
            submitter_label = f"@{submitter.username} ({submitter.tg_user_id})"

    moderator_label = "-"
    if moderator is not None:
        moderator_label = f"{moderator.tg_user_id}"
        if moderator.username:
            moderator_label = f"@{moderator.username} ({moderator.tg_user_id})"

    resolution_note = item.resolution_note or "Одобрено модератором"
    title = f"[{type_label}] Feedback #{item.id}"
    body = (
        "## Источник\n"
        f"- Feedback ID: {item.id}\n"
        f"- Тип: {item.type}\n"
        f"- Автор: {submitter_label}\n"
        f"- Модератор: {moderator_label}\n"
        f"- Награда: {item.reward_points} points\n"
        "\n"
        "## Сообщение пользователя\n"
        f"{item.content}\n"
        "\n"
        "## Решение модерации\n"
        f"{resolution_note}\n"
    )
    return FeedbackIssueDraft(
        title=title,
        body=body,
        labels=labels_for_feedback_type(feedback_type),
    )
