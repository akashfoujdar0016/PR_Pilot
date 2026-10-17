#!/usr/bin/env python3
"""
PR Pilot — Changelog Generator
================================
Triggered when a pull request is merged into the base branch.
Uses Google Gemini to generate a human-readable changelog entry
and appends it to CHANGELOG.md in the repository.

Configuration is loaded from config/review_config.yml.
"""

import os
import re
import sys
import json
import time
import datetime
import yaml
import google.generativeai as genai

from github_utils import (
    fetch_pr_files,
    fetch_pr_commits,
    post_issue_comment,
    gh_post,
    GITHUB_API,
)

# ──────────────────────────────────────────────────────────────────────────────
# Environment
# ──────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
GITHUB_TOKEN    = os.environ["GITHUB_TOKEN"]        # noqa: F841 (used in github_utils)
PR_NUMBER       = os.environ["PR_NUMBER"]
PR_TITLE        = os.environ.get("PR_TITLE", "")
PR_BODY         = os.environ.get("PR_BODY", "") or "(no description)"
PR_AUTHOR       = os.environ.get("PR_AUTHOR", "unknown")
MERGED_AT       = os.environ.get("MERGED_AT", "")
BASE_BRANCH     = os.environ.get("BASE_BRANCH", "main")
REPO_FULL_NAME  = os.environ["REPO_FULL_NAME"]

MAX_DIFF_CHARS  = 40_000

# ──────────────────────────────────────────────────────────────────────────────
# Load configuration
# ──────────────────────────────────────────────────────────────────────────────
_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "review_config.yml"
)

def load_config() -> dict:
    """Load review_config.yml, falling back to safe defaults."""
    defaults = {
        "gemini": {
            "model":             "gemini-2.5-flash",
            "temperature":       0.3,
            "max_output_tokens": 2048,
        },
        "changelog": {
            "output_file":    "CHANGELOG.md",
            "tracked_types":  ["feat", "fix", "security", "perf", "breaking"],
            "post_comment":   True,
        },
    }
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            file_cfg = yaml.safe_load(f) or {}
        for section, values in file_cfg.items():
            if isinstance(values, dict) and section in defaults:
                defaults[section].update(values)
            else:
                defaults[section] = values
        print(f"✅ Loaded config from {_CONFIG_PATH}")
    except FileNotFoundError:
        print(f"⚠️  Config file not found at {_CONFIG_PATH} — using defaults.")
    except yaml.YAMLError as exc:
        print(f"⚠️  Config parse error: {exc} — using defaults.")
    return defaults

CFG = load_config()

GEMINI_CFG    = CFG["gemini"]
CHANGELOG_CFG = CFG["changelog"]
CHANGELOG_FILE = CHANGELOG_CFG["output_file"]

# ──────────────────────────────────────────────────────────────────────────────
# Step 1 — Fetch merged PR diff
# ──────────────────────────────────────────────────────────────────────────────
def build_diff_summary(files: list[dict]) -> str:
    lines = []
    for f in files:
        lines.append(
            f"- `{f['filename']}` ({f['status']}, +{f.get('additions',0)}/-{f.get('deletions',0)})"
        )
    return "\n".join(lines)


def build_diff_content(files: list[dict]) -> str:
    sections = []
    for f in files:
        patch = f.get("patch", "")
        if not patch:
            continue
        sections.append(f"### {f['filename']}\n```diff\n{patch}\n```")
    full = "\n\n".join(sections)
    if len(full) > MAX_DIFF_CHARS:
        full = full[:MAX_DIFF_CHARS] + "\n\n... [truncated] ..."
    return full


# ──────────────────────────────────────────────────────────────────────────────
# Step 2 — Gemini generates changelog entry
# ──────────────────────────────────────────────────────────────────────────────
CHANGELOG_PROMPT = """\
You are **PR Pilot**, an AI assistant that writes concise, professional changelog entries for software projects.

A pull request has just been merged. Generate a changelog entry for it.

## Merged PR Details
- **PR #:** {pr_number}
- **Title:** {title}
- **Author:** @{author}
- **Merged into:** `{base}`
- **Merged at:** {merged_at}
- **Description:**
{body}

## Files Changed
{file_summary}

## Diff (for context)
{diff}

---

Return a **single JSON object** (no markdown fences) with this schema:

```json
{{
  "type": "feat | fix | docs | style | refactor | perf | test | chore | security",
  "scope": "optional scope (e.g., api, auth, ui) or null",
  "breaking": <true | false>,
  "short_description": "One-line imperative summary (max 72 chars, no period at end)",
  "changelog_entry": "Full changelog entry in GitHub Markdown. Use bullet points. Include: what changed, why it matters, any migration notes if breaking.",
  "highlights": ["Short highlight 1", "Short highlight 2"]
}}
```

Rules:
- Use conventional commit type (feat, fix, docs, etc.)
- short_description must start with an imperative verb (Add, Fix, Update, Remove, etc.)
- changelog_entry should be 2–6 bullet points, clear and user-facing
- Return ONLY the raw JSON object — no surrounding text or fences.
"""


def call_gemini_changelog(prompt: str, retries: int = 3) -> dict:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_CFG["model"],
        generation_config=genai.GenerationConfig(
            temperature=GEMINI_CFG["temperature"],
            max_output_tokens=GEMINI_CFG["max_output_tokens"],
        ),
    )
    for attempt in range(1, retries + 1):
        try:
            print(f"🤖 Calling {GEMINI_CFG['model']} for changelog (attempt {attempt}/{retries})...")
            response = model.generate_content(prompt)
            raw = response.text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
            return json.loads(raw)
        except Exception as e:
            print(f"⚠️  Attempt {attempt} failed: {e}")
            if attempt < retries:
                wait = 5 * attempt
                print(f"   Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


# ──────────────────────────────────────────────────────────────────────────────
# Step 3 — Build and write the changelog entry
# ──────────────────────────────────────────────────────────────────────────────
TYPE_EMOJI = {
    "feat":     "✨",
    "fix":      "🐛",
    "docs":     "📄",
    "style":    "🎨",
    "refactor": "♻️",
    "perf":     "⚡",
    "test":     "🧪",
    "chore":    "🔧",
    "security": "🔒",
}


def format_changelog_entry(entry: dict, merged_date: str) -> str:
    change_type  = entry.get("type", "chore")
    scope        = entry.get("scope")
    breaking     = entry.get("breaking", False)
    short_desc   = entry.get("short_description", PR_TITLE)
    full_entry   = entry.get("changelog_entry", "")
    emoji        = TYPE_EMOJI.get(change_type, "🔧")

    scope_part   = f"({scope})" if scope else ""
    breaking_tag = " **[BREAKING CHANGE]**" if breaking else ""
    header       = f"### {emoji} `{change_type}{scope_part}`{breaking_tag}: {short_desc}"
    meta         = (
        f"_PR [#{PR_NUMBER}](https://github.com/{REPO_FULL_NAME}/pull/{PR_NUMBER}) "
        f"by @{PR_AUTHOR} — {merged_date}_"
    )

    return f"{header}\n\n{meta}\n\n{full_entry}\n"


def load_existing_changelog() -> str:
    if os.path.exists(CHANGELOG_FILE):
        with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def write_changelog(new_entry: str, merged_date: str):
    existing = load_existing_changelog()

    section_header = f"## [{merged_date}]"

    if section_header in existing:
        # Insert new entry right after the section header line
        updated = existing.replace(
            section_header,
            f"{section_header}\n\n{new_entry}",
            1,
        )
    else:
        # Prepend a new dated section
        if existing.startswith("# "):
            lines = existing.split("\n", 2)
            updated = (
                f"{lines[0]}\n\n"
                f"{section_header}\n\n{new_entry}\n\n"
                f"{lines[2] if len(lines) > 2 else ''}"
            )
        else:
            updated = (
                f"# Changelog\n\n"
                f"All notable changes to this project are documented here.\n\n"
                f"{section_header}\n\n{new_entry}\n\n{existing}"
            )

    with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
        f.write(updated)

    print(f"📄 {CHANGELOG_FILE} updated with entry for PR #{PR_NUMBER}")


# ──────────────────────────────────────────────────────────────────────────────
# Step 4 — Post a comment to the PR with the changelog preview
# ──────────────────────────────────────────────────────────────────────────────
def post_changelog_comment(entry: dict, formatted_entry: str):
    if not CHANGELOG_CFG.get("post_comment", True):
        print("ℹ️  post_comment is disabled in config — skipping PR comment.")
        return

    highlights = entry.get("highlights", [])
    highlights_md = (
        "\n".join(f"  - {h}" for h in highlights)
        if highlights
        else "  - (see entry below)"
    )

    body = (
        f"## 📄 PR Pilot — Changelog Generated\n\n"
        f"A changelog entry has been automatically added to `{CHANGELOG_FILE}`.\n\n"
        f"### Highlights\n{highlights_md}\n\n"
        f"### Preview\n\n"
        f"<details>\n<summary>Click to expand</summary>\n\n"
        f"{formatted_entry}\n"
        f"</details>\n\n"
        f"---\n"
        f"<sub>🤖 Generated by **PR Pilot** using {GEMINI_CFG['model']}</sub>"
    )

    post_issue_comment(REPO_FULL_NAME, PR_NUMBER, body)
    print("💬 Changelog preview comment posted to PR.")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"📄 PR Pilot Changelog — PR #{PR_NUMBER} merged into {BASE_BRANCH}")
    print(f"{'='*60}\n")

    # Parse merge date
    try:
        merged_dt   = datetime.datetime.fromisoformat(MERGED_AT.replace("Z", "+00:00"))
        merged_date = merged_dt.strftime("%Y-%m-%d")
    except Exception:
        merged_date = datetime.date.today().isoformat()

    # Fetch files
    print("📥 Fetching merged PR files...")
    files = fetch_pr_files(REPO_FULL_NAME, PR_NUMBER)
    if not files:
        print("ℹ️  No files in this PR — skipping changelog.")
        sys.exit(0)

    file_summary = build_diff_summary(files)
    diff_content = build_diff_content(files)
    print(f"   {len(files)} file(s) changed")

    # Call Gemini
    prompt = CHANGELOG_PROMPT.format(
        pr_number=PR_NUMBER,
        title=PR_TITLE,
        author=PR_AUTHOR,
        base=BASE_BRANCH,
        merged_at=MERGED_AT,
        body=PR_BODY,
        file_summary=file_summary,
        diff=diff_content,
    )
    entry = call_gemini_changelog(prompt)
    print(f"   Type: {entry.get('type')} | Breaking: {entry.get('breaking')}")
    print(f"   Description: {entry.get('short_description')}")

    # Write changelog
    formatted = format_changelog_entry(entry, merged_date)
    write_changelog(formatted, merged_date)

    # Post comment
    post_changelog_comment(entry, formatted)

    print(f"\n✅ Changelog generation complete for PR #{PR_NUMBER}!\n")


if __name__ == "__main__":
    main()
