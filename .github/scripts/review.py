#!/usr/bin/env python3
"""
Multi-agent PR code review entrypoint.
Reviews Readability, Correctness, Maintainability, Performance, and Reliability,
and posts inline comments on specific code lines.
"""

import sys
import time
from config import PR_NUMBER, REPO, MAX_DIFF_CHARS, AGENTS, PROMPTS_DIR
from github_service import gh_get_diff, gh_post_review
from diff_parser import parse_diff, map_comments_to_positions
from ai_service import gemini


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
