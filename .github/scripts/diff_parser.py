import logging
from schemas import AgentReviewResult

_log = logging.getLogger(__name__)


# ponytail: splits unified diff into chunks <= max_chars by file/line boundaries
def split_diff_into_chunks(diff_text: str, max_chars: int) -> list[str]:
    if max_chars <= 0 or len(diff_text) <= max_chars:
        return [diff_text]

    lines = diff_text.splitlines(keepends=True)
    chunks = []
    current_chunk = []
    current_len = 0

    for line in lines:
        if line.startswith("diff --git ") and current_len >= max_chars and current_chunk:
            chunks.append("".join(current_chunk))
            current_chunk = []
            current_len = 0
        elif current_len + len(line) > max_chars and current_chunk:
            chunks.append("".join(current_chunk))
            current_chunk = []
            current_len = 0

        current_chunk.append(line)
        current_len += len(line)

    if current_chunk:
        chunks.append("".join(current_chunk))

    return chunks


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

    if current_path and current_hunk_lines:
        hunks.append(DiffHunk(current_hunk_lines))
    if current_path:
        files.append(DiffFile(current_path, hunks))

    return files


def map_comments_to_positions(
    diff_files: list[DiffFile],
    ai_result: AgentReviewResult,
    agent_label: str,
) -> list[dict]:
    """Map AI lineContent to diff positions for inline comments."""
    comments = []
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

        file_path, position = matches[0]
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
