import json
import ssl
import urllib.request
import urllib.error
from config import REPO, PR_NUMBER, GITHUB_TOKEN

try:
    import certifi
    _ssl_context = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _ssl_context = None


def gh_get_diff() -> str:
    """Fetch PR diff from GitHub REST API."""
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    kwargs = {"timeout": 120}
    if _ssl_context:
        kwargs["context"] = _ssl_context
    with urllib.request.urlopen(req, **kwargs) as r:
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
    kwargs = {"timeout": 120}
    if _ssl_context:
        kwargs["context"] = _ssl_context
    with urllib.request.urlopen(req, **kwargs) as r:
        json.loads(r.read())
    print(f"  Posted review with {len(comments)} inline comment(s).")


def gh_post_issue_comment(body: str) -> None:
    """Post a regular issue comment."""
    url = f"https://api.github.com/repos/{REPO}/issues/{PR_NUMBER}/comments"
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    kwargs = {"timeout": 120}
    if _ssl_context:
        kwargs["context"] = _ssl_context
    with urllib.request.urlopen(req, **kwargs) as r:
        json.loads(r.read())

