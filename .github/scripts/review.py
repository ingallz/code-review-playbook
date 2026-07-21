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
MODEL          = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
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


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, Exception):
        msg = str(exc).lower()
        return any(k in msg for k in ("503", "502", "429", "unavailable", "quota", "resource exhausted"))
    return False


_client = genai.Client(api_key=GEMINI_API_KEY)


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=10, max=90, jitter=5),
    before_sleep=before_sleep_log(_log, logging.WARNING),
    reraise=True,
)
def gemini(system_prompt: str, user_content: str) -> str:
    response = _client.models.generate_content(
        model=MODEL,
        contents=user_content,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            http_options=genai_types.HttpOptions(timeout=180_000),  # ms
        ),
    )
    return response.text


# ── Formatting ────────────────────────────────────────────────────────────────

SEVERITY_EMOJI = {"blocker": "🔴", "warning": "🟡", "nit": "🔵"}
SCORE_EMOJI    = {5: "🟢", 4: "🟢", 3: "🟡", 2: "🔴", 1: "🔴"}


def score_bar(score: int) -> str:
    filled = "█" * score
    empty  = "░" * (5 - score)
    return f"`{filled}{empty}` {score}/5"


def format_agent_section(label: str, score_key: str, result: dict) -> str:
    issues  = result.get("issues", [])
    summary = result.get("summary", {})
    score   = summary.get(score_key, "?")
    comment = summary.get("overall_comment", "")

    emoji = SCORE_EMOJI.get(score, "⚪") if isinstance(score, int) else "⚪"
    lines = [f"### {emoji} {label}  {score_bar(score) if isinstance(score, int) else ''}"]
    lines.append(f"> {comment}")
    lines.append("")

    if not issues:
        lines.append("✅ No issues found.")
    else:
        for iss in issues:
            sev   = iss.get("severity", "nit")
            icon  = SEVERITY_EMOJI.get(sev, "⚪")
            loc   = iss.get("location", "")
            rule  = iss.get("rule", "")
            desc  = iss.get("description", "")
            fix   = iss.get("suggested_fix", "")
            # extra context fields (vary per agent)
            extra_key = next((k for k in ("failure_scenario","risk_scenario","scale_impact") if k in iss), None)
            extra = f"\n  > 💡 *{iss[extra_key]}*" if extra_key else ""

            lines.append(f"- {icon} **{sev.upper()}** `{rule}` — **{loc}**")
            lines.append(f"  {desc}{extra}")
            if fix:
                lines.append(f"  🔧 {fix}")
    return "\n".join(lines)


def build_comment(agent_sections: list[tuple[str, str, dict]]) -> str:
    header = (
        "## 🤖 Automated Code Review\n\n"
        "This review was generated by 5 independent AI agents, "
        "each focused on a different quality dimension.\n\n"
        "---\n"
    )
    body = "\n\n---\n\n".join(
        format_agent_section(label, score_key, result)
        for label, score_key, result in agent_sections
    )
    footer = "\n\n---\n*Powered by Gemini · [review playbook](.github/prompts)*"
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
            raw = gemini(system_prompt, user_msg)
            result = json.loads(raw)
        except Exception as exc:
            print(f"  ⚠️  {label} agent failed: {exc}", file=sys.stderr)
            result = {"issues": [], "summary": {score_key: "?", "overall_comment": f"Agent error: {exc}"}}
        agent_sections.append((label, score_key, result))

    comment_body = build_comment(agent_sections)

    print("Posting review comment…")
    gh_post(f"issues/{PR_NUMBER}/comments", {"body": comment_body})
    print("Done ✅")


if __name__ == "__main__":
    main()
