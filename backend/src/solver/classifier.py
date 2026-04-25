"""
Classify a DripsIssue as EASY, MEDIUM, or HARD based on title, description,
and labels. Used to prioritise the work queue and skip overly complex tasks.
"""

import re
from src.drips.models import DripsIssue

# Ordered from most-specific to least — first match wins.
_HARD_PATTERNS = [
    r"\bweb3\b", r"\bblockchain\b", r"\bwallet\b", r"\bsmart.?contract\b",
    r"\bsolidity\b", r"\bonchain\b", r"\bon.chain\b", r"\bdefi\b",
    r"\bmulti.?system\b", r"\bmigrat\w+\b", r"\brefactor\w*\b",
    r"\barchitecture\b", r"\binfrastructure\b", r"\bdevops\b",
    r"\bdocker\b", r"\bkubernetes\b", r"\bk8s\b",
    r"\bauth(entication|oriz)?\b", r"\boauth\b", r"\bjwt\b",
    r"\bdatabase.?schema\b", r"\bdata.?model\b",
    r"\blarge.?scale\b", r"\boverhaul\b", r"\brewrite\b",
]

_MEDIUM_PATTERNS = [
    r"\bapi\b", r"\bendpoint\b", r"\bintegrat\w+\b",
    r"\bperformance\b", r"\boptimiz\w+\b", r"\bcach\w+\b",
    r"\btest\w*\b", r"\bci\b", r"\bpipeline\b",
    r"\bconfig\w*\b", r"\benv\w*\b", r"\bsetting\w*\b",
    r"\bmulti.?file\b", r"\bseveral.?file\b",
    r"\bbackend\b", r"\bserver\b", r"\bservice\b",
]

_EASY_PATTERNS = [
    r"\bui\b", r"\bbutton\b", r"\bstyle\b", r"\bcss\b", r"\blayout\b",
    r"\btypo\b", r"\bspelling\b", r"\bcopy\b", r"\btext\b", r"\blabel\b",
    r"\broute\b", r"\blink\b", r"\bredirect\b", r"\bnavigat\w+\b",
    r"\bfrontend\b", r"\bcomponent\b", r"\bpage\b", r"\bview\b",
    r"\bsmall\b", r"\bminor\b", r"\bsimple\b", r"\bquick\b",
    r"\bfix\b", r"\bbug\b", r"\bcrash\b", r"\berror.?message\b",
    r"\bsingle.?file\b", r"\bone.?line\b",
]

_HARD_LABELS   = {"web3", "blockchain", "infrastructure", "architecture", "security", "migration"}
_MEDIUM_LABELS = {"enhancement", "feature", "performance", "testing", "backend"}
_EASY_LABELS   = {"bug", "good first issue", "ui", "frontend", "documentation", "typo"}

PRIORITY = {"EASY": 0, "MEDIUM": 1, "HARD": 2}


def classify(issue: DripsIssue) -> str:
    text = f"{issue.title} {issue.description}".lower()
    labels = {lbl.lower() for lbl in issue.labels}

    # Label-based shortcuts
    if labels & _HARD_LABELS:
        return "HARD"
    if labels & _EASY_LABELS and not (labels & _MEDIUM_LABELS):
        return "EASY"

    # Pattern matching — HARD takes precedence
    if _matches(text, _HARD_PATTERNS):
        return "HARD"
    if _matches(text, _EASY_PATTERNS) and not _matches(text, _MEDIUM_PATTERNS):
        return "EASY"
    return "MEDIUM"


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text) for p in patterns)
