#!/usr/bin/env python3
"""
Multi-agent PR code review using Gemini.
Each agent reviews one dimension: readability, correctness, maintainability, performance, reliability.

Usage:
    GEMINI_API_KEY=... GITHUB_TOKEN=... python review.py
    (env vars PR_NUMBER, GITHUB_REPOSITORY also required — set automatically by GH Actions)
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import google.genai as genai
import google.genai.types as genai_types
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
    before_sleep_log,
)
import logging

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GITHUB_TOKEN   = os.environ["GITHUB_TOKEN"]
REPO           = os.environ["GITHUB_REPOSITORY"]          # "owner/repo"
PR_NUMBER      = os.environ["PR_NUMBER"]
MAX_DIFF_CHARS = int(os.environ.get("MAX_DIFF_CHARS", "30000"))
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

AGENTS = [
    ("readability",    "Readability",              "readability_score"),
    ("correctness",    "Correctness",              "correctness_score"),
    ("maintainability","Maintainability",          "maintainability_score"),
    ("performance",    "Performance & Scalability","performance_score"),
    ("reliability",    "Reliability & Security",   "reliability_security_score"),
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def gh_get(path: str) -> dict | list:
    url = f"https://api.github.com/repos/{REPO}/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req) as r:
        return r.read().decode()


def gh_post(path: str, body: dict) -> None:
    url = f"https://api.github.com/repos/{REPO}/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


from typing import List, Literal, Optional, Any
from pydantic import BaseModel, Field, model_validator

# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class RuleCheck(BaseModel):
    rule_id: str = ""
    status: str = "no_violation"
    note: str = ""


class Issue(BaseModel):
    location: str = "Unknown location"
    rule: str = "N/A"
    severity: str = "nit"
    description: str = ""
    risk_scenario: Optional[str] = None
    failure_scenario: Optional[str] = None
    scale_impact: Optional[str] = None
    suggested_fix: Optional[str] = None

    @property
    def impact_details(self) -> Optional[str]:
        return self.risk_scenario or self.failure_scenario or self.scale_impact


class AgentSummary(BaseModel):
    score: int = 5
    overall_comment: str = ""

    @model_validator(mode="before")
    @classmethod
    def extract_score(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normalize agent-specific score keys into unified 'score'
            for key in (
                "score",
                "readability_score",
                "correctness_score",
                "maintainability_score",
                "performance_score",
                "reliability_security_score",
            ):
                if key in data and data[key] is not None:
                    try:
                        data["score"] = int(data[key])
                        break
                    except (ValueError, TypeError):
                        pass
        return data


class AgentReviewResult(BaseModel):
    issues: List[Issue] = Field(default_factory=list)
    rules_checked: List[RuleCheck] = Field(default_factory=list)
    summary: AgentSummary = Field(default_factory=AgentSummary)


# ── Model & Rate Limit Config ──────────────────────────────────────────────────

MODEL_HIERARCHY = [
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite"
]

RPM_LIMITS = {
    "gemini-3.5-flash": 5,
    "gemini-3-flash":  5,
    "gemini-3.1-flash-lite": 15,
    "gemini-2.5-flash": 5,
    "gemini-2.5-flash-lite": 10,
}

current_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
last_request_time = 0.0


def get_rate_limit_delay(model: str) -> float:
    rpm = RPM_LIMITS.get(model, 10)
    return 60.0 / rpm


def derank_model() -> bool:
    global current_model
    if current_model in MODEL_HIERARCHY:
        idx = MODEL_HIERARCHY.index(current_model)
        if idx + 1 < len(MODEL_HIERARCHY):
            old = current_model
            current_model = MODEL_HIERARCHY[idx + 1]
            _log.warning(f"🔻 Deranking model from {old} to {current_model} due to rate limits.")
            return True
    elif MODEL_HIERARCHY:
        old = current_model
        current_model = "gemini-2.5-flash"
        _log.warning(f"🔻 Deranking model from {old} to {current_model}.")
        return True
    return False


def enforce_rate_limit():
    global last_request_time
    delay = get_rate_limit_delay(current_model)
    elapsed = time.time() - last_request_time
    if elapsed < delay:
        wait = delay - elapsed
        _log.info(f"⏳ Rate limiting: waiting {wait:.1f}s before request (model: {current_model})")
        time.sleep(wait)
    last_request_time = time.time()


_client = genai.Client(api_key=GEMINI_API_KEY)


def gemini(system_prompt: str, user_content: str, max_retries: int = 4) -> AgentReviewResult:
    global current_model
    
    for attempt in range(max_retries + 1):
        enforce_rate_limit()
        try:
            _log.info(f"Sending request using model: {current_model} (attempt {attempt + 1})")
            response = _client.models.generate_content(
                model=current_model,
                contents=user_content,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=AgentReviewResult,
                    http_options=genai_types.HttpOptions(timeout=180_000),
                ),
            )
            # Pydantic validation via model_validate_json
            return AgentReviewResult.model_validate_json(response.text)
        except Exception as exc:
            msg = str(exc)
            _log.warning(f"Attempt {attempt + 1} failed with model {current_model}: {msg[:200]}")
            
            if ("429" in msg or "resource_exhausted" in msg.lower()) and derank_model():
                _log.info(f"Retrying immediately with deranked model {current_model}...")
                continue
            
            if attempt < max_retries:
                wait_sec = 5.0 * (2 ** attempt)
                _log.warning(f"Waiting {wait_sec:.1f}s before retry...")
                time.sleep(wait_sec)
            else:
                raise exc


# ── Dashboard Formatting ──────────────────────────────────────────────────────

SEVERITY_EMOJI = {"blocker": "🔴", "warning": "🟡", "nit": "🔵"}
SCORE_EMOJI    = {5: "🟢", 4: "🟢", 3: "🟡", 2: "🔴", 1: "🔴"}


def score_bar(score: int) -> str:
    if not isinstance(score, int) or score <= 0:
        return "`░░░░░` N/A"
    filled = "█" * score
    empty  = "░" * (5 - score)
    return f"`{filled}{empty}` **{score}/5**"


def format_agent_section(label: str, result: AgentReviewResult) -> str:
    issues  = result.issues
    summary = result.summary
    score   = summary.score
    comment = summary.overall_comment

    emoji = SCORE_EMOJI.get(score, "⚪") if isinstance(score, int) and score > 0 else "⚪"
    issue_count = len(issues)
    badge = f"{issue_count} issue{'s' if issue_count != 1 else ''}" if issue_count > 0 else "PASSED"

    lines = [
        f"<details open>",
        f"<summary><b>{emoji} {label}</b> &nbsp;|&nbsp; Score: {score_bar(score)} &nbsp;|&nbsp; <code>{badge}</code></summary>\n",
        f"> **Summary**: {comment}\n"
    ]

    if not issues:
        lines.append("✅ *No violations found for this dimension.*\n")
    else:
        for iss in issues:
            sev_clean = iss.severity.lower() if isinstance(iss.severity, str) else "nit"
            icon  = SEVERITY_EMOJI.get(sev_clean, "🔵")

            extra = f"\n> 💡 **Impact/Risk**: *{iss.impact_details}*" if iss.impact_details else ""

            lines.append(f"#### {icon} `{sev_clean.upper()}` — `{iss.rule}` in **{iss.location}**")
            lines.append(f"{iss.description}{extra}\n")
            if iss.suggested_fix:
                lines.append("```suggestion")
                lines.append(iss.suggested_fix)
                lines.append("```\n")

    lines.append("</details>\n")
    return "\n".join(lines)


def build_comment(agent_sections: list[tuple[str, AgentReviewResult]]) -> str:
    table_rows = []
    total_issues = 0
    blockers = 0

    for label, result in agent_sections:
        summary = result.summary
        score   = summary.score
        issues  = result.issues
        total_issues += len(issues)
        blockers += sum(1 for i in issues if getattr(i, "severity", "").lower() == "blocker")

        emoji = SCORE_EMOJI.get(score, "⚪") if isinstance(score, int) and score > 0 else "⚪"
        issues_str = f"`{len(issues)} issue(s)`" if len(issues) > 0 else "✅ Pass"
        table_rows.append(f"| {emoji} **{label}** | {score_bar(score)} | {issues_str} |")

    status_header = "🔴 **Action Required**" if blockers > 0 else ("🟡 **Minor Improvements**" if total_issues > 0 else "🟢 **Approved**")

    header = (
        f"## 🤖 Multi-Agent AI PR Review\n\n"
        f"### Status: {status_header}\n\n"
        f"| Dimension | Quality Score | Status |\n"
        f"| :--- | :--- | :--- |\n" +
        "\n".join(table_rows) +
        "\n\n---\n\n"
        "### 🔍 Detailed Analysis by Dimension\n\n"
    )

    body = "\n".join(
        format_agent_section(label, result)
        for label, result in agent_sections
    )

    footer = (
        "\n---\n"
        "<sub>Powered by <b>Gemini Multi-Agent Playbook</b> · 5 Specialized Quality Reviewers</sub>"
    )
    return header + body + footer


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Fetching diff for PR #{PR_NUMBER} in {REPO}…")
    diff = gh_get(f"pulls/{PR_NUMBER}.diff")
    if len(diff) > MAX_DIFF_CHARS:
        print(f"Diff truncated from {len(diff)} → {MAX_DIFF_CHARS} chars")
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated — file too large]"

    user_msg = f"Review the following code diff:\n\n```diff\n{diff}\n```"

    agent_sections = []
    for i, (slug, label, score_key) in enumerate(AGENTS):
        if i > 0:
            time.sleep(10)  # pacing: give the model breathing room between agents
        prompt_path = PROMPTS_DIR / f"{slug}.md"
        system_prompt = prompt_path.read_text()
        print(f"Running {label} agent…")
        try:
            result = gemini(system_prompt, user_msg)
        except Exception as exc:
            print(f"  ⚠️  {label} agent failed: {exc}", file=sys.stderr)
            result = AgentReviewResult(
                summary=AgentSummary(score=0, overall_comment=f"Agent error: {exc}")
            )
        agent_sections.append((label, result))

    comment_body = build_comment(agent_sections)

    print("Posting review comment…")
    gh_post(f"issues/{PR_NUMBER}/comments", {"body": comment_body})
    print("Done ✅")


if __name__ == "__main__":
    main()
