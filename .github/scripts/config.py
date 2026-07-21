import os
from pathlib import Path

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
REPO           = os.environ.get("GITHUB_REPOSITORY", "")
PR_NUMBER      = os.environ.get("PR_NUMBER", "")
MAX_DIFF_CHARS = int(os.environ.get("MAX_DIFF_CHARS", "60000"))
PROMPTS_DIR    = Path(__file__).parent.parent / "prompts"

AGENTS = [
    ("readability",    "Readability"),
    ("correctness",    "Correctness"),
    ("maintainability","Maintainability"),
    ("performance",    "Performance & Scalability"),
    ("reliability",    "Reliability & Security"),
]

MODEL_HIERARCHY = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

RPM_LIMITS = {
    "gemini-3.5-flash": 5,
    "gemini-3-flash-preview":  5,
    "gemini-3.1-flash-lite": 15,
    "gemini-2.5-flash": 5,
    "gemini-2.5-flash-lite": 10,
}
