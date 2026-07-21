#!/usr/bin/env python3
"""
Multi-agent PR code review entrypoint.
Reviews Readability, Correctness, Maintainability, Performance, and Reliability,
and posts inline comments on specific code lines.
"""

import sys
import time
from config import PR_NUMBER, REPO, MAX_DIFF_CHARS, AGENTS, PROMPTS_DIR
from github_service import gh_get_diff, gh_post_review, gh_post_issue_comment
from diff_parser import parse_diff, map_comments_to_positions
from ai_service import gemini


def main():
    print(f"Fetching diff for PR #{PR_NUMBER} in {REPO}…")
    diff = gh_get_diff()
    diff_len = len(diff)

    # ponytail: abort review if diff exceeds limit, post PR comment and terminate cleanly
    if MAX_DIFF_CHARS > 0 and diff_len > MAX_DIFF_CHARS:
        print(f"⚠️ Diff size ({diff_len:,} chars) exceeds maximum allowed limit ({MAX_DIFF_CHARS:,} chars).")
        comment_body = (
            f"⚠️ **Code Review Aborted**: PR diff size ({diff_len:,} characters) "
            f"exceeds the maximum allowed limit of {MAX_DIFF_CHARS:,} characters.\n\n"
            f"Có quá nhiều thay đổi trong PR này. Vui lòng chia nhỏ PR thành các PR nhỏ hơn để tự động review."
        )
        try:
            gh_post_issue_comment(comment_body)
            print("Posted comment on PR and terminating review process.")
        except Exception as exc:
            print(f"Failed to post PR issue comment: {exc}", file=sys.stderr)
        sys.exit(0)

    print(f"Loaded diff ({diff_len:,} chars)")

    diff_files = parse_diff(diff)
    print(f"Parsed {len(diff_files)} file(s) from diff")

    user_msg = f"Review the following code diff:\n\n```diff\n{diff}\n```"
    total = len(AGENTS)

    for i, (slug, label) in enumerate(AGENTS):
        if i > 0:
            time.sleep(10)
        prompt_path = PROMPTS_DIR / f"{slug}.md"
        system_prompt = prompt_path.read_text()
        print(f"\n==================================================")
        print(f"🤖 [Rule {i+1}/{total}] Running agent: {label} ({slug})")
        print(f"==================================================")
        try:
            result = gemini(system_prompt, user_msg, agent_name=label)
            print(f"  Got {len(result.reviews)} review(s)")
        except Exception as exc:
            print(f"  ⚠️  [Rule {i+1}/{total}] {label} agent failed: {exc}", file=sys.stderr)
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
