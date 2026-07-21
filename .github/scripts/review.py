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
from diff_parser import parse_diff, map_comments_to_positions, split_diff_into_chunks
from ai_service import gemini


def main():
    print(f"Fetching diff for PR #{PR_NUMBER} in {REPO}…")
    diff = gh_get_diff()
    diff_len = len(diff)

    chunks = split_diff_into_chunks(diff, MAX_DIFF_CHARS)
    num_chunks = len(chunks)

    # ponytail: notify if diff is large, then continue processing all chunks sequentially without stopping
    if num_chunks > 1:
        print(f"⚠️ Diff size ({diff_len:,} chars) exceeds chunk limit ({MAX_DIFF_CHARS:,} chars). Splitting into {num_chunks} chunks.")
        comment_body = (
            f"ℹ️ **PR Diff Size Notification**: PR diff size ({diff_len:,} characters) "
            f"exceeds single-batch limit of {MAX_DIFF_CHARS:,} characters.\n\n"
            f"Hệ thống sẽ tự động chia diff thành {num_chunks} phần và thực hiện review tuần tự toàn bộ code."
        )
        try:
            gh_post_issue_comment(comment_body)
            print("Posted diff notification comment on PR.")
        except Exception as exc:
            print(f"Failed to post PR issue comment: {exc}", file=sys.stderr)
    else:
        print(f"Loaded diff ({diff_len:,} chars)")

    total_agents = len(AGENTS)

    for chunk_idx, chunk in enumerate(chunks, 1):
        if num_chunks > 1:
            print(f"\n==================================================")
            print(f"📦 [Chunk {chunk_idx}/{num_chunks}] Size: {len(chunk):,} chars")
            print(f"==================================================")

        diff_files = parse_diff(chunk)
        print(f"Parsed {len(diff_files)} file(s) in chunk {chunk_idx}")

        user_msg = f"Review the following code diff:\n\n```diff\n{chunk}\n```"

        for i, (slug, label) in enumerate(AGENTS):
            if i > 0 or chunk_idx > 1:
                time.sleep(10)
            prompt_path = PROMPTS_DIR / f"{slug}.md"
            system_prompt = prompt_path.read_text()
            agent_display = f"{label} (Part {chunk_idx}/{num_chunks})" if num_chunks > 1 else label

            print(f"\n  --------------------------------------------------")
            print(f"  🤖 [Rule {i+1}/{total_agents}] Running agent: {agent_display}")
            print(f"  --------------------------------------------------")
            try:
                result = gemini(system_prompt, user_msg, agent_name=agent_display)
                print(f"    Got {len(result.reviews)} review(s)")
            except Exception as exc:
                print(f"    ⚠️  [Rule {i+1}/{total_agents}] {agent_display} agent failed: {exc}", file=sys.stderr)
                continue

            comments = map_comments_to_positions(diff_files, result, label)
            if comments:
                print(f"    Posting {len(comments)} inline comment(s)…")
                try:
                    gh_post_review(comments)
                except Exception as exc:
                    print(f"    ⚠️  Failed to post review: {exc}", file=sys.stderr)
            else:
                print(f"    ✅ No issues found by {agent_display} agent.")

    print("\nDone ✅")


if __name__ == "__main__":
    main()
