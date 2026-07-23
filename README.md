# 🤖 Multi-Agent PR Review with Gemini AI

An automated GitHub Action for Pull Request code reviews powered by **Google Gemini AI** using a **Multi-Agent Architecture (5 Independent AI Reviewers)**.

---

## 🌟 Key Features

- 🤖 **5 Independent Specialist AI Agents**:
  1. **Readability**: Code cleanliness, formatting, naming conventions, and readability.
  2. **Correctness**: Logic bugs, edge cases, type errors, and potential runtime crashes.
  3. **Maintainability**: Architectural structure, code complexity, modularity, and extensibility.
  4. **Performance & Scalability**: Performance bottlenecks, resource leaks, and query optimization.
  5. **Reliability & Security**: Security vulnerabilities, data leakage, and exception handling.

- 📦 **Smart Auto-Chunking for Large PRs**:
  - Automatically splits large PR diffs into manageable chunks ($\le 35,000$ characters / ~350–400 lines of code) to review 100% of the diff sequentially without losing context or hitting API rate limits.
  - Posts a helpful PR notification comment when a large diff is detected.

- 🛡️ **Auto Model Fallback & Reset (Deranking)**:
  - Automatically deranks to fallback models (`gemini-3.5-flash` → `gemini-2.5-flash` → `gemini-2.5-flash-lite`...) upon encountering API errors or rate limits (`429`).
  - Resets back to the primary preferred model for each subsequent agent rule execution.

- 💬 **Direct Line-by-Line Inline Comments**:
  - Posts inline review comments directly onto specific code lines in the GitHub PR **Files changed** tab.

---

## 🚀 Setup & Usage Guide

### Step 1: Add `GEMINI_API_KEY` to Repository Secrets

1. Get a free or paid API key from [Google AI Studio](https://aistudio.google.com/).
2. Navigate to your GitHub Repository → **Settings** → **Secrets and variables** → **Actions**.
3. Click **New repository secret**:
   - **Name**: `GEMINI_API_KEY`
   - **Secret**: Paste your Gemini API key.

---

### Step 2: Create a GitHub Workflow

Create a file named `.github/workflows/pr-review.yml` in your repository:

```yaml
name: Multi-Agent PR Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  issues: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run Multi-Agent Code Review
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          GEMINI_MODEL: "gemini-3.5-flash"
          MAX_DIFF_CHARS: "35000"
        run: python .github/scripts/review.py
```

*Note: You can also use this repository as a composite GitHub Action:*

```yaml
- name: Run Code Review Action
  uses: ingallz/code-review-playbook@main
  with:
    model: "gemini-3.5-flash"
    max_diff_chars: "35000"
  env:
    GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## ⚙️ Configuration Reference

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | *(Required)* | Google Gemini API key from AI Studio. |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Gemini model name to use. Supported models: `gemini-3.5-flash`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.0-flash`. |
| `MAX_DIFF_CHARS` | `35000` | Maximum character length for each diff chunk. Diffs larger than this threshold will be automatically split into $N$ parts and reviewed sequentially. |

---

## 📁 Repository Structure

```text
.
├── .github/
│   ├── scripts/
│   │   ├── ai_service.py       # Gemini API client, retries & model deranking
│   │   ├── config.py           # Environment variables & model hierarchy
│   │   ├── diff_parser.py      # Diff parsing, chunking & line mapping
│   │   ├── github_service.py   # GitHub REST API service (Diff & Comments)
│   │   ├── review.py           # Main entrypoint orchestrating the 5 review agents
│   │   └── schemas.py          # Definitive Pydantic schemas for AI outputs
│   ├── prompts/                # System prompts for the 5 specialized agents
│   │   ├── correctness.md
│   │   ├── maintainability.md
│   │   ├── performance.md
│   │   ├── readability.md
│   │   └── reliability.md
│   └── workflows/
│       └── pr-review.yml       # GitHub Actions workflow definition
├── action.yml                  # Composite GitHub Action manifest
└── README.md
```

---

## 📄 License

MIT License.
