#!/usr/bin/env python3
"""
PR Pilot — Shared GitHub API Utilities
=======================================
Common helpers used by both pr_review.py and changelog_generator.py.
Import this module instead of duplicating HTTP logic in each script.
"""

import os
import requests

GITHUB_API = "https://api.github.com"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

GH_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept":        "application/vnd.github.v3+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def gh_get(url: str, params: dict = None) -> dict | list:
    """GET a GitHub API endpoint, raise on non-2xx."""
    resp = requests.get(url, headers=GH_HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def gh_post(url: str, payload: dict) -> dict:
    """POST to a GitHub API endpoint, raise on non-2xx."""
    resp = requests.post(url, headers=GH_HEADERS, json=payload, timeout=30)
    if not resp.ok:
        print(f"⚠️  GitHub POST failed [{resp.status_code}]: {resp.text[:500]}")
        resp.raise_for_status()
    return resp.json()


def gh_patch(url: str, payload: dict) -> dict:
    """PATCH a GitHub API endpoint, raise on non-2xx."""
    resp = requests.patch(url, headers=GH_HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def set_commit_status(
    repo: str,
    sha: str,
    state: str,        # "pending" | "success" | "failure" | "error"
    description: str,
    context: str = "PR Pilot / AI Code Review",
    target_url: str = "",
):
    """
    Set a GitHub commit status (the coloured dot on a PR).

    Args:
        repo:        Full repo name, e.g. "owner/repo"
        sha:         The HEAD commit SHA of the PR
        state:       One of pending / success / failure / error
        description: Short human-readable message (≤140 chars)
        context:     Status context name shown in GitHub UI
        target_url:  Optional link shown on the status badge
    """
    url = f"{GITHUB_API}/repos/{repo}/statuses/{sha}"
    payload: dict = {
        "state":       state,
        "description": description[:140],
        "context":     context,
    }
    if target_url:
        payload["target_url"] = target_url

    try:
        result = gh_post(url, payload)
        print(f"🔵 Commit status set → {state}: {description}")
        return result
    except Exception as e:
        # Non-fatal — status posting should never break the review
        print(f"⚠️  Could not set commit status: {e}")
        return {}


def fetch_pr_files(repo: str, pr_number: str) -> list[dict]:
    """Fetch all changed files for a PR (handles pagination)."""
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files"
    files = []
    page = 1
    while True:
        batch = gh_get(url, params={"per_page": 100, "page": page})
        if not batch:
            break
        files.extend(batch)
        page += 1
    return files


def fetch_pr_commits(repo: str, pr_number: str) -> list[dict]:
    """Fetch all commits for a PR."""
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/commits"
    return gh_get(url, params={"per_page": 100})


def post_issue_comment(repo: str, pr_number: str, body: str) -> dict:
    """Post a plain comment on a PR / issue."""
    url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    return gh_post(url, {"body": body})


def apply_labels(repo: str, pr_number: str, labels: list[str], color_map: dict):
    """
    Apply a list of labels to a PR, creating any that don't already exist.

    Args:
        repo:      Full repo name
        pr_number: PR number string
        labels:    List of label names to apply
        color_map: Dict mapping label name → hex color (without #)
    """
    if not labels:
        return

    existing = {
        lbl["name"]
        for lbl in gh_get(
            f"{GITHUB_API}/repos/{repo}/labels", {"per_page": 100}
        )
    }

    for label in labels:
        if label not in existing:
            try:
                gh_post(
                    f"{GITHUB_API}/repos/{repo}/labels",
                    {
                        "name":  label,
                        "color": color_map.get(label, "ededed"),
                    },
                )
            except Exception as e:
                print(f"⚠️  Could not create label '{label}': {e}")

    issue_url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/labels"
    try:
        gh_post(issue_url, {"labels": labels})
        print(f"🏷️  Labels applied: {labels}")
    except Exception as e:
        print(f"⚠️  Could not apply labels: {e}")
