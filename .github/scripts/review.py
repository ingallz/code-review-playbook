#!/usr/bin/env python3
"""
Multi-agent PR code review using Gemini.
Each agent reviews one dimension and posts inline comments on specific lines.

Usage:
    GEMINI_API_KEY=... GITHUB_TOKEN=... python review.py
    (env vars PR_NUMBER, GITHUB_REPOSITORY also required — set automatically by GH Actions)
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional, Any

import google.genai as genai
import google.genai.types as genai_types
from pydantic import BaseModel, Field
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
    ("readability",    "Readability"),
    ("correctness",    "Correctness"),
    ("maintainability","Maintainability"),
    ("performance",    "Performance & Scalability"),
    ("reliability",    "Reliability & Security"),
]


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class InlineComment(BaseModel):
    """One review comment on a specific line of code."""
    lineContent: str = Field(description="The EXACT line from the diff including the leading '+' character")
    reviewComment: str = Field(description="GitHub Markdown review comment explaining the issue")
    category: str = Field(default="suggestion", description="One of: bug, security, performance, style, suggestion")


class AgentReviewResult(BaseModel):
    """Output from one review agent."""
    reviews: List[InlineComment] = Field(default_factory=list)


# ── Diff Parser ───────────────────────────────────────────────────────────────

class DiffHunk:
    def __init__(self, lines: list[str]):
        self.lines = lines

class DiffFile:
    def __init__(self, path: str, hunks: list[DiffHunk]):
        self.path = path
        self.hunks = hunks


def parse_diff(diff_text: str) -> list[DiffFile]:
    """Parse unified diff into files and hunks."""
    files = []
    current_path = None
    current_hunk_lines = []
    hunks = []

    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            # Save previous file
            if current_path and current_hunk_lines:
                hunks.append(DiffHunk(current_hunk_lines))
            if current_path:
                files.append(DiffFile(current_path, hunks))
            current_path = None
            current_hunk_lines = []
            hunks = []
        elif line.startswith("+++ b/"):
            current_path = line[6:]
        elif line.startswith("@@ "):
            if current_hunk_lines:
                hunks.append(DiffHunk(current_hunk_lines))
            current_hunk_lines = []
        elif current_path and not line.startswith("--- "):
            if line.startswith("+") or line.startswith("-") or line.startswith(" ") or line == "":
                current_hunk_lines.append(line)

    # Save last file
    if current_path and current_hunk_lines:
        hunks.append(DiffHunk(current_hunk_lines))
    if current_path:
        files.append(DiffFile(current_path, hunks))

    return files


# ── GitHub API ────────────────────────────────────────────────────────────────

def gh_get_diff() -> str:
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req) as r:
        return r.read().decode()


def gh_post_review(comments: list[dict]) -> None:
    """Post inline review comments using the Pull Request Review API."""
    if not comments:
        print("  No comments to post.")
        return
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}/reviews"
    body = {
        "body": "",
        "event": "COMMENT",
        "comments": comments,
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req) as r:
        resp = json.loads(r.read())
    print(f"  Posted review with {len(comments)} inline comment(s).")


def gh_post_issue_comment(body: str) -> None:
    """Post a regular issue comment (for summary)."""
    url = f"https://api.github.com/repos/{REPO}/issues/{PR_NUMBER}/comments"
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req) as r:
        json.loads(r.read())


# ── Comment Mapping ──────────────────────────────────────────────────────────

def map_comments_to_positions(
    diff_files: list[DiffFile],
    ai_result: AgentReviewResult,
    agent_label: str,
) -> list[dict]:
    """Map AI lineContent to diff positions for inline comments, like gemini-code-review-action."""
    comments = []
    # Build a lookup: normalized line -> (file_path, position)
    # position = 1-indexed offset from the first @@ line in the file's diff
    line_map: dict[str, list[tuple[str, int]]] = {}
    for diff_file in diff_files:
        pos = 0
        for hunk in diff_file.hunks:
            for raw_line in hunk.lines:
                pos += 1
                if raw_line.startswith("+"):
                    normalized = raw_line.strip().replace("  ", " ")
                    if normalized not in line_map:
                        line_map[normalized] = []
                    line_map[normalized].append((diff_file.path, pos))

    for review in ai_result.reviews:
        line = review.lineContent
        if not line or not line.strip().startswith("+"):
            continue
        normalized = line.strip().replace("  ", " ")
        matches = line_map.get(normalized, [])
        if not matches:
            _log.debug(f"  Skipping hallucinated line: {line[:60]}")
            continue

        file_path, position = matches[0]  # take first match
        category_emoji = {
            "bug": "🐛", "security": "🔒", "performance": "⚡",
            "style": "🎨", "suggestion": "💡",
        }.get(review.category, "💡")

        body = f"{category_emoji} **[{agent_label}]** {review.reviewComment}"
        comments.append({
            "path": file_path,
            "position": position,
            "body": body,
        })

    return comments


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
                    http_options=genai_types.HttpOptions(timeout=300_000),  # 5 mins
                ),
            )
            return AgentReviewResult.model_validate_json(response.text)
        except Exception as exc:
            msg = str(exc)
            _log.warning(f"Attempt {attempt + 1} failed with model {current_model}: {msg[:200]}")

            is_transient_or_rate = any(
                err in msg.lower()
                for err in ("429", "resource_exhausted", "504", "503", "gateway timeout", "timed out", "timeout")
            )
            if is_transient_or_rate and derank_model():
                _log.info(f"Retrying immediately with deranked model {current_model}...")
                continue

            if attempt < max_retries:
                wait_sec = 5.0 * (2 ** attempt)
                _log.warning(f"Waiting {wait_sec:.1f}s before retry...")
                time.sleep(wait_sec)
            else:
                raise exc


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Fetching diff for PR #{PR_NUMBER} in {REPO}…")
    diff = gh_get_diff()
    if len(diff) > MAX_DIFF_CHARS:
        print(f"Diff truncated from {len(diff)} → {MAX_DIFF_CHARS} chars")
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated — file too large]"

    diff_files = parse_diff(diff)
    print(f"Parsed {len(diff_files)} file(s) from diff")

    user_msg = f"Review the following code diff:\n\n```diff\n{diff}\n```"
    total = len(AGENTS)

    for i, (slug, label) in enumerate(AGENTS):
        if i > 0:
            time.sleep(10)
        prompt_path = PROMPTS_DIR / f"{slug}.md"
        system_prompt = prompt_path.read_text()
        print(f"Running {label} agent ({i+1}/{total})…")
        try:
            result = gemini(system_prompt, user_msg)
            print(f"  Got {len(result.reviews)} review(s)")
        except Exception as exc:
            print(f"  ⚠️  {label} agent failed: {exc}", file=sys.stderr)
            continue

        # Map AI reviews to inline diff positions & post immediately
        comments = map_comments_to_positions(diff_files, result, label)
        if comments:
            print(f"  Posting {len(comments)} inline comment(s)…")
            try:
                gh_post_review(comments)
            except Exception as exc:
                print(f"  ⚠️  Failed to post review: {exc}", file=sys.stderr)
        else:
            print(f"  ✅ No issues found by {label} agent.")

    print("Done ✅")


if __name__ == "__main__":
    main()
