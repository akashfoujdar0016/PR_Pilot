#!/usr/bin/env python3
"""
PR Pilot — AI Code Review Agent
================================
Fetches the diff of a pull request, sends it to Google Gemini
for a deep review, and posts structured inline comments + a summary
review back to the GitHub pull request.

Configuration is loaded from config/review_config.yml so behaviour
can be tweaked without touching this script.
"""

import os
import re
import sys
import json
import time
import textwrap
import yaml
import google.generativeai as genai

from github_utils import (
    GITHUB_API,
    fetch_pr_files,
    post_issue_comment,
    apply_labels,
    set_commit_status,
    gh_get,
    gh_post,
)

# ──────────────────────────────────────────────────────────────────────────────
# Environment
# ──────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
GITHUB_TOKEN    = os.environ["GITHUB_TOKEN"]        # noqa: F841 (used in github_utils)
PR_NUMBER       = os.environ["PR_NUMBER"]
PR_TITLE        = os.environ.get("PR_TITLE", "")
PR_BODY         = os.environ.get("PR_BODY", "") or "(no description provided)"
PR_AUTHOR       = os.environ.get("PR_AUTHOR", "unknown")
BASE_BRANCH     = os.environ.get("BASE_BRANCH", "main")
HEAD_BRANCH     = os.environ.get("HEAD_BRANCH", "")
HEAD_SHA        = os.environ.get("HEAD_SHA", "")          # for commit status
REPO_FULL_NAME  = os.environ["REPO_FULL_NAME"]

# ──────────────────────────────────────────────────────────────────────────────
# Load configuration
# ──────────────────────────────────────────────────────────────────────────────
_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "review_config.yml"
)

def load_config() -> dict:
    """Load review_config.yml, falling back to safe defaults."""
    defaults = {
        "review": {
            "min_quality_score":  7,
            "require_tests":      True,
            "require_docs":       False,
            "skip_patterns":      ["*.lock", "*.min.js", "*.min.css", "dist/*", "build/*"],
            "max_inline_comments": 15,
            "focus_areas":        ["bugs", "security", "performance", "tests", "documentation", "style"],
        },
        "gemini": {
            "model":             "gemini-2.5-flash",
            "temperature":       0.2,
            "max_output_tokens": 8192,
        },
        "labels": {
            "enabled":   True,
            "color_map": {
                "bug":            "d73a4a",
                "enhancement":    "a2eeef",
                "documentation":  "0075ca",
                "style":          "e4e669",
                "performance":    "fb8500",
                "security":       "e11d48",
                "needs-tests":    "f97316",
                "breaking-change":"7c3aed",
                "chore":          "ededed",
            },
        },
    }
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            file_cfg = yaml.safe_load(f) or {}
        # Deep-merge file config over defaults
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

REVIEW_CFG  = CFG["review"]
GEMINI_CFG  = CFG["gemini"]
LABELS_CFG  = CFG["labels"]

MAX_DIFF_CHARS     = 80_000
MAX_INLINE         = REVIEW_CFG["max_inline_comments"]
MIN_QUALITY_SCORE  = REVIEW_CFG["min_quality_score"]
REQUIRE_TESTS      = REVIEW_CFG["require_tests"]
REQUIRE_DOCS       = REVIEW_CFG["require_docs"]
SKIP_PATTERNS      = REVIEW_CFG["skip_patterns"]

# ──────────────────────────────────────────────────────────────────────────────
# File filtering
# ──────────────────────────────────────────────────────────────────────────────
import fnmatch

def should_skip_file(filename: str) -> bool:
    """Return True if this file matches any skip pattern in the config."""
    return any(fnmatch.fnmatch(filename, pat) for pat in SKIP_PATTERNS)


# ──────────────────────────────────────────────────────────────────────────────
# Step 1 — Fetch PR metadata and diff
# ──────────────────────────────────────────────────────────────────────────────
def build_diff_content(files: list[dict]) -> tuple[str, list[dict]]:
    """
    Returns (diff_text, file_summary_list).
    Skips files matching skip_patterns. diff_text is capped at MAX_DIFF_CHARS.
    """
    sections = []
    file_summaries = []

    for f in files:
        filename  = f["filename"]
        if should_skip_file(filename):
            print(f"   ⏭️  Skipping {filename} (matches skip pattern)")
            continue

        status    = f["status"]           # added / modified / removed / renamed
        additions = f.get("additions", 0)
        deletions = f.get("deletions", 0)
        patch     = f.get("patch", "")    # absent for binary files

        file_summaries.append({
            "filename":  filename,
            "status":    status,
            "additions": additions,
            "deletions": deletions,
        })

        block = [
            f"### {status.upper()} — {filename}",
            f"(+{additions} / -{deletions} lines)",
        ]
        if patch:
            block.append(f"```diff\n{patch}\n```")
        else:
            block.append("_(binary or empty file — no patch available)_")

        sections.append("\n".join(block))

    full_diff = "\n\n".join(sections)
    if len(full_diff) > MAX_DIFF_CHARS:
        full_diff = full_diff[:MAX_DIFF_CHARS] + "\n\n... [diff truncated — too large] ..."

    return full_diff, file_summaries


# ──────────────────────────────────────────────────────────────────────────────
# Step 2 — Ask Gemini for a structured review
# ──────────────────────────────────────────────────────────────────────────────
FOCUS_AREA_DESCRIPTIONS = {
    "bugs":          "🐛 **Bugs & Logic Errors** — incorrect conditions, off-by-one, unhandled edge cases",
    "security":      "🔒 **Security** — injection risks, exposed secrets, improper auth/input validation",
    "performance":   "⚡ **Performance** — unnecessary loops, N+1 queries, inefficient algorithms",
    "tests":         "🧪 **Test Coverage** — are new code paths tested? edge cases covered?",
    "documentation": "📄 **Documentation** — are new functions/classes documented?",
    "style":         "🎨 **Style & Consistency** — naming conventions, formatting, dead code",
}

def build_focus_areas_section() -> str:
    areas = REVIEW_CFG.get("focus_areas", list(FOCUS_AREA_DESCRIPTIONS.keys()))
    lines = []
    for i, area in enumerate(areas, 1):
        desc = FOCUS_AREA_DESCRIPTIONS.get(area, f"• {area}")
        lines.append(f"{i}. {desc}")
    return "\n".join(lines)


REVIEW_PROMPT_TEMPLATE = """\
You are **PR Pilot**, an expert AI code reviewer embedded inside GitHub.
Your job is to perform a thorough, actionable code review of the pull request below.

## Pull Request Context
- **Title:** {title}
- **Author:** {author}
- **Base branch:** {base} ← **Head branch:** {head}
- **Description:**
{body}

## Changed Files Summary
{file_summary}

## Full Diff
{diff}

---

## Your Task
Analyze the diff carefully and return a **single JSON object** (no markdown fences around it) with the following schema:

```json
{{
  "summary": "2–4 sentence executive summary of what this PR does.",
  "quality_score": <integer 1–10>,
  "verdict": "APPROVE | REQUEST_CHANGES | COMMENT",
  "has_tests": <true | false>,
  "has_docs": <true | false>,
  "suggested_labels": ["list", "of", "github", "labels"],
  "inline_comments": [
    {{
      "path": "relative/file/path",
      "line": <positive integer — the NEW file line number in the diff>,
      "severity": "bug | warning | style | suggestion",
      "body": "Concise, actionable comment. Include a code suggestion if helpful."
    }}
  ],
  "overall_feedback": "Detailed overall feedback in GitHub-flavored Markdown. Use headings, bullet points, and code blocks."
}}
```

### Review Focus Areas (in priority order)
{focus_areas}

### Rules
- Only create inline_comments for lines that are actually present in the diff.
- Keep inline comment bodies concise and actionable (< 120 words each).
- Post at most {max_inline_comments} inline comments.
- Do NOT fabricate file names or line numbers — only reference what is in the diff.
- Return ONLY the raw JSON — no leading/trailing text, no markdown fences.
"""


def parse_gemini_json(raw: str) -> dict:
    """Extract and parse the JSON block from Gemini's response."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def call_gemini(prompt: str, retries: int = 3) -> dict:
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
            print(f"🤖 Calling {GEMINI_CFG['model']} (attempt {attempt}/{retries})...")
            response = model.generate_content(prompt)
            return parse_gemini_json(response.text)
        except Exception as e:
            print(f"⚠️  Gemini attempt {attempt} failed: {e}")
            if attempt < retries:
                wait = 5 * attempt
                print(f"   Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


# ──────────────────────────────────────────────────────────────────────────────
# Step 3 — Quality gate enforcement
# ──────────────────────────────────────────────────────────────────────────────
def check_quality_gates(review: dict) -> list[str]:
    """
    Evaluate configured quality gates and return a list of failure messages.
    An empty list means all gates passed.
    """
    failures = []

    score = review.get("quality_score", 0)
    if isinstance(score, int) and score < MIN_QUALITY_SCORE:
        failures.append(
            f"Quality score {score}/10 is below the required minimum of {MIN_QUALITY_SCORE}/10."
        )

    if REQUIRE_TESTS and not review.get("has_tests", False):
        failures.append(
            "No tests detected. This PR must include test coverage (require_tests = true)."
        )

    if REQUIRE_DOCS and not review.get("has_docs", False):
        failures.append(
            "No documentation update detected. This PR must include docs (require_docs = true)."
        )

    return failures


# ──────────────────────────────────────────────────────────────────────────────
# Step 4 — Post the review back to GitHub
# ──────────────────────────────────────────────────────────────────────────────
SEVERITY_EMOJI = {
    "bug":        "🐛",
    "warning":    "⚠️",
    "style":      "🎨",
    "suggestion": "💡",
}


def build_review_body(review: dict, file_summaries: list[dict], gate_failures: list[str]) -> str:
    score   = review.get("quality_score", "?")
    verdict = review.get("verdict", "COMMENT")
    has_tests = review.get("has_tests", False)
    has_docs  = review.get("has_docs", False)
    labels  = review.get("suggested_labels", [])
    summary = review.get("summary", "")
    overall = review.get("overall_feedback", "")

    score_bar = "█" * score + "░" * (10 - score) if isinstance(score, int) else "?"

    files_md = "\n".join(
        f"  - `{f['filename']}` ({f['status']}, +{f['additions']}/-{f['deletions']})"
        for f in file_summaries
    )

    verdict_icon = {"APPROVE": "✅", "REQUEST_CHANGES": "🚫", "COMMENT": "💬"}.get(verdict, "💬")

    # Quality gate section
    gates_md = ""
    if gate_failures:
        gate_lines = "\n".join(f"- ❌ {msg}" for msg in gate_failures)
        gates_md = f"\n\n---\n\n### 🚧 Quality Gate Failures\n\n{gate_lines}"

    body = textwrap.dedent(f"""\
        ## 🤖 PR Pilot Review — AI Code Analysis

        > Powered by **{GEMINI_CFG['model']}** · Automated by [PR Pilot](https://github.com/{REPO_FULL_NAME})

        ---

        ### 📊 Review Snapshot

        | Metric | Result |
        |---|---|
        | **Quality Score** | `{score}/10`  {score_bar} |
        | **Verdict** | {verdict_icon} `{verdict}` |
        | **Tests Included** | {'✅ Yes' if has_tests else '❌ No — please add tests'} |
        | **Docs Updated** | {'✅ Yes' if has_docs else '❌ No — please update documentation'} |
        | **Suggested Labels** | {', '.join(f'`{l}`' for l in labels) if labels else '_none_'} |

        ---

        ### 📝 Summary

        {summary}

        ---

        ### 📁 Files Changed

        {files_md}

        ---

        ### 🔍 Detailed Feedback

        {overall}{gates_md}

        ---

        <sub>🤖 This review was generated automatically by **PR Pilot** using {GEMINI_CFG['model']}.
        It is a first-pass analysis — please use your own judgment alongside this feedback.</sub>
    """)
    return body


def post_github_review(review: dict, review_body: str):
    """Post inline comments + summary as a single GitHub pull request review."""
    raw_comments = review.get("inline_comments", [])

    # Enforce max inline comments from config
    raw_comments = raw_comments[:MAX_INLINE]

    github_comments = []
    for c in raw_comments:
        if not c.get("path") or not c.get("line"):
            continue
        try:
            line = int(c["line"])
        except (ValueError, TypeError):
            continue

        emoji = SEVERITY_EMOJI.get(c.get("severity", "suggestion"), "💡")
        body  = f"{emoji} **{c.get('severity', 'suggestion').capitalize()}**\n\n{c.get('body', '')}"
        github_comments.append({
            "path": c["path"],
            "line": line,
            "side": "RIGHT",
            "body": body,
        })

    payload = {
        "body":     review_body,
        "event":    "COMMENT",
        "comments": github_comments,
    }

    url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/pulls/{PR_NUMBER}/reviews"
    try:
        result = gh_post(url, payload)
        print(f"✅ Review posted: {result.get('html_url', 'N/A')}")
        return result
    except Exception as e:
        print(f"⚠️  PR Reviews API failed ({e}). Falling back to issue comment...")
        result = post_issue_comment(REPO_FULL_NAME, PR_NUMBER, review_body)
        print("✅ Fallback comment posted successfully.")
        return result


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"🤖 PR Pilot — Starting review for PR #{PR_NUMBER}")
    print(f"   Repo : {REPO_FULL_NAME}")
    print(f"   Title: {PR_TITLE}")
    print(f"{'='*60}\n")

    # Mark the commit as "pending" while we work
    if HEAD_SHA:
        set_commit_status(
            REPO_FULL_NAME, HEAD_SHA,
            state="pending",
            description="PR Pilot is reviewing your changes…",
        )

    try:
        # 1. Fetch diff
        print("📥 Fetching PR files and diff...")
        files = fetch_pr_files(REPO_FULL_NAME, PR_NUMBER)
        if not files:
            print("ℹ️  No files changed — nothing to review.")
            if HEAD_SHA:
                set_commit_status(
                    REPO_FULL_NAME, HEAD_SHA,
                    state="success",
                    description="No files changed — nothing to review.",
                )
            sys.exit(0)

        diff_content, file_summaries = build_diff_content(files)
        print(f"   Found {len(files)} changed file(s) ({len(file_summaries)} reviewed), "
              f"diff length: {len(diff_content):,} chars")

        if not file_summaries:
            print("ℹ️  All changed files are in skip patterns — nothing to review.")
            if HEAD_SHA:
                set_commit_status(
                    REPO_FULL_NAME, HEAD_SHA,
                    state="success",
                    description="All files skipped by config — no review needed.",
                )
            sys.exit(0)

        # 2. Build prompt and call Gemini
        file_summary_text = "\n".join(
            f"- `{f['filename']}` ({f['status']}, +{f['additions']}/-{f['deletions']})"
            for f in file_summaries
        )

        prompt = REVIEW_PROMPT_TEMPLATE.format(
            title=PR_TITLE,
            author=PR_AUTHOR,
            base=BASE_BRANCH,
            head=HEAD_BRANCH,
            body=PR_BODY,
            file_summary=file_summary_text,
            diff=diff_content,
            focus_areas=build_focus_areas_section(),
            max_inline_comments=MAX_INLINE,
        )

        review = call_gemini(prompt)
        print(f"   Quality Score  : {review.get('quality_score')}/10")
        print(f"   Verdict        : {review.get('verdict')}")
        print(f"   Inline comments: {len(review.get('inline_comments', []))}")

        # 3. Quality gate enforcement
        gate_failures = check_quality_gates(review)
        if gate_failures:
            print(f"\n🚧 Quality gate failures ({len(gate_failures)}):")
            for msg in gate_failures:
                print(f"   • {msg}")

        # 4. Build and post GitHub review
        print("\n📤 Posting review to GitHub...")
        review_body = build_review_body(review, file_summaries, gate_failures)
        post_github_review(review, review_body)

        # 5. Apply labels (if enabled in config)
        if LABELS_CFG.get("enabled", True):
            apply_labels(
                REPO_FULL_NAME,
                PR_NUMBER,
                review.get("suggested_labels", []),
                LABELS_CFG.get("color_map", {}),
            )

        # 6. Set final commit status
        if HEAD_SHA:
            if gate_failures:
                set_commit_status(
                    REPO_FULL_NAME, HEAD_SHA,
                    state="failure",
                    description=f"PR Pilot: {len(gate_failures)} quality gate(s) failed. "
                                f"Score {review.get('quality_score')}/10.",
                )
            else:
                score = review.get("quality_score", 0)
                set_commit_status(
                    REPO_FULL_NAME, HEAD_SHA,
                    state="success",
                    description=f"PR Pilot: Score {score}/10 · {review.get('verdict')} ✅",
                )

        print(f"\n✅ PR Pilot review complete for PR #{PR_NUMBER}!\n")

    except Exception as exc:
        print(f"\n❌ PR Pilot encountered a fatal error: {exc}")
        if HEAD_SHA:
            set_commit_status(
                REPO_FULL_NAME, HEAD_SHA,
                state="error",
                description=f"PR Pilot error: {str(exc)[:100]}",
            )
        raise


if __name__ == "__main__":
    main()
