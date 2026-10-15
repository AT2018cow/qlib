"""GitHub Git Data API helper: push multiple files in a single commit.

Used by daily_cron to push signal CSV + chart JSON atomically (one
commit = one Actions trigger = no concurrency-cancel race that caused
the 09-22 chart data to be missing from the Pages deployment).
"""
import base64
import json
import urllib.request

BASE = "https://api.github.com"


def _req(token, method, url, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    })
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read()) if resp.status != 204 else None


def push_files(token, repo, branch, files, message):
    """Push multiple files in a single commit via the Git Data API.

    Args:
        token: GitHub PAT with Contents read/write on the repo
        repo: "owner/name"
        branch: e.g. "main"
        files: {path: content_str} — content as plain text
        message: commit message
    """
    # 1. Get the current HEAD commit's tree SHA
    head = _req(token, "GET", f"{BASE}/repos/{repo}/git/ref/heads/{branch}")
    base_tree_sha = head["object"]["sha"]

    # 2. Get the tree of that commit
    commit = _req(token, "GET", f"{BASE}/repos/{repo}/git/commits/{base_tree_sha}")
    base_tree = commit["tree"]["sha"]

    # 3. Create new tree entries for each file
    tree_items = []
    for path, content in files.items():
        blob = _req(token, "POST", f"{BASE}/repos/{repo}/git/blobs", {
            "content": content,
            "encoding": "utf-8",
        })
        tree_items.append({
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })

    # 4. Create a new tree (based on HEAD's tree + our changes)
    new_tree = _req(token, "POST", f"{BASE}/repos/{repo}/git/trees", {
        "base_tree": base_tree,
        "tree": tree_items,
    })

    # 5. Create a commit pointing to the new tree, parent = HEAD
    new_commit = _req(token, "POST", f"{BASE}/repos/{repo}/git/commits", {
        "message": message,
        "tree": new_tree["sha"],
        "parents": [base_tree_sha],
    })

    # 6. Update the branch ref
    _req(token, "PATCH", f"{BASE}/repos/{repo}/git/refs/heads/{branch}", {
        "sha": new_commit["sha"],
    })

    return new_commit["sha"]
