from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DripsIssue:
    id: str                    # "{owner}/{repo}#{number}"
    title: str
    description: str
    github_issue_url: str
    github_repo_url: str
    repo_owner: str
    repo_name: str
    issue_number: int
    reward: Optional[str] = None
    labels: list[str] = field(default_factory=list)
