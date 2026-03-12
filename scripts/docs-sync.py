#!/usr/bin/env python3
"""
docs-sync: Auto-update Timepoint docs from upstream repo commits.

Runs as a GitHub Action cron job. For each public timepointai repo:
1. Fetches commits since last sync
2. Picks the best free model available on OpenRouter
3. Asks the model to diff commits against current docs
4. Opens a PR if docs need updating
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# --- Config ---

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
ORG = "timepointai"

# Map: repo name -> doc files it can touch
REPO_DOC_MAP = {
    "timepoint-flash": ["api-reference/flash.mdx", "products/flash.mdx"],
    "timepoint-pro": ["api-reference/pro.mdx", "products/pro.mdx"],
    "timepoint-clockchain": ["api-reference/clockchain.mdx", "products/clockchain.mdx"],
    "proteus": ["products/proteus.mdx"],
    "timepoint-tdf": ["products/tdf.mdx"],
    "snag-bench": ["products/snag-bench.mdx"],
}

DOCS_REPO = f"{ORG}/docs"
STATE_FILE = Path("scripts/.sync-state.json")
LOOKBACK_HOURS = int(os.environ.get("SYNC_LOOKBACK_HOURS", "24"))

GH = httpx.Client(
    base_url="https://api.github.com",
    headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    },
    timeout=30,
)

# --- Model Selection ---


def pick_best_free_model() -> str:
    """Query OpenRouter for free models, return the best one available today."""
    # Preferred free models in priority order (these rotate on OpenRouter)
    preferred = [
        "deepseek/deepseek-r1-0528:free",
        "deepseek/deepseek-chat-v3-0324:free",
        "qwen/qwen3-235b-a22b:free",
        "qwen/qwen3-30b-a3b:free",
        "meta-llama/llama-4-maverick:free",
        "meta-llama/llama-4-scout:free",
        "google/gemini-2.5-pro-exp-03-25:free",
        "mistralai/mistral-small-3.1-24b-instruct:free",
    ]

    try:
        resp = httpx.get(
            "https://openrouter.ai/api/v1/models",
            timeout=15,
        )
        resp.raise_for_status()
        models = resp.json().get("data", [])

        # Build set of available free model IDs
        free_ids = set()
        for m in models:
            pricing = m.get("pricing", {})
            prompt_cost = float(pricing.get("prompt", "1"))
            completion_cost = float(pricing.get("completion", "1"))
            if prompt_cost == 0 and completion_cost == 0:
                free_ids.add(m["id"])

        # Pick first preferred that's available and free
        for model_id in preferred:
            if model_id in free_ids:
                print(f"Selected model: {model_id}")
                return model_id

        # Fallback: pick any free model with large context
        for m in models:
            if m["id"] in free_ids:
                ctx = m.get("context_length", 0)
                if ctx >= 32000:
                    print(f"Selected fallback model: {m['id']}")
                    return m["id"]

    except Exception as e:
        print(f"OpenRouter model listing failed: {e}")

    # Last resort fallback
    fallback = "deepseek/deepseek-r1-0528:free"
    print(f"Using hardcoded fallback: {fallback}")
    return fallback


def llm_complete(model: str, system: str, user: str) -> str:
    """Call OpenRouter chat completion."""
    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/timepointai/docs",
            "X-Title": "Timepoint Docs Sync",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 8192,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# --- GitHub Helpers ---


def get_commits_since(repo: str, since: str) -> list[dict]:
    """Fetch commits from a repo since a given ISO timestamp."""
    resp = GH.get(f"/repos/{ORG}/{repo}/commits", params={"since": since, "per_page": 100})
    if resp.status_code == 404:
        return []  # repo might be private or not exist
    resp.raise_for_status()
    return resp.json()


def get_commit_diff(repo: str, sha: str) -> str:
    """Get the patch for a specific commit."""
    resp = GH.get(
        f"/repos/{ORG}/{repo}/commits/{sha}",
        headers={"Accept": "application/vnd.github.diff"},
    )
    if resp.status_code != 200:
        return ""
    # Truncate huge diffs
    text = resp.text
    if len(text) > 15000:
        text = text[:15000] + "\n... (truncated)"
    return text


def read_doc_file(path: str) -> str:
    """Read a doc file from the local repo."""
    full = Path(path)
    if full.exists():
        return full.read_text()
    return ""


def create_pr(branch: str, title: str, body: str, files: dict[str, str]) -> str | None:
    """Create a branch, commit file changes, and open a PR. Returns PR URL."""
    # Get main branch SHA
    resp = GH.get(f"/repos/{DOCS_REPO}/git/ref/heads/main")
    resp.raise_for_status()
    main_sha = resp.json()["object"]["sha"]

    # Create branch
    resp = GH.post(
        f"/repos/{DOCS_REPO}/git/refs",
        json={"ref": f"refs/heads/{branch}", "sha": main_sha},
    )
    if resp.status_code == 422:
        # Branch exists, skip
        print(f"Branch {branch} already exists, skipping")
        return None
    resp.raise_for_status()

    # Get the tree
    resp = GH.get(f"/repos/{DOCS_REPO}/git/trees/{main_sha}")
    resp.raise_for_status()

    # Create blobs and tree entries
    tree_entries = []
    for filepath, content in files.items():
        blob_resp = GH.post(
            f"/repos/{DOCS_REPO}/git/blobs",
            json={"content": content, "encoding": "utf-8"},
        )
        blob_resp.raise_for_status()
        tree_entries.append({
            "path": filepath,
            "mode": "100644",
            "type": "blob",
            "sha": blob_resp.json()["sha"],
        })

    # Create tree
    tree_resp = GH.post(
        f"/repos/{DOCS_REPO}/git/trees",
        json={"base_tree": main_sha, "tree": tree_entries},
    )
    tree_resp.raise_for_status()

    # Create commit
    commit_resp = GH.post(
        f"/repos/{DOCS_REPO}/git/commits",
        json={
            "message": title,
            "tree": tree_resp.json()["sha"],
            "parents": [main_sha],
        },
    )
    commit_resp.raise_for_status()

    # Update branch ref
    GH.patch(
        f"/repos/{DOCS_REPO}/git/refs/heads/{branch}",
        json={"sha": commit_resp.json()["sha"]},
    ).raise_for_status()

    # Create PR
    pr_resp = GH.post(
        f"/repos/{DOCS_REPO}/pulls",
        json={
            "title": title,
            "body": body,
            "head": branch,
            "base": "main",
        },
    )
    pr_resp.raise_for_status()
    pr_url = pr_resp.json()["html_url"]
    print(f"Created PR: {pr_url}")
    return pr_url


# --- State ---


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


# --- Main ---

SYSTEM_PROMPT = """You maintain public API documentation for Timepoint AI.
You will receive recent commit diffs from a source repo and the current documentation files.

Your job:
1. Identify if any commits contain changes that should be reflected in the docs (new features, API changes, config changes, bug fixes that affect behavior).
2. If yes, output the COMPLETE updated file content for each doc file that needs changes.
3. If no docs need updating, respond with exactly: NO_CHANGES

Rules:
- Only update docs for things that are clearly demonstrated in the diffs. Do not speculate.
- Preserve the existing Mintlify MDX frontmatter and structure.
- Keep the same writing style as the existing docs — concise, technical, no fluff.
- Do not add AI attribution, emojis, or "last updated" dates.
- Do not remove existing content unless it's factually wrong based on the diffs.

Output format when changes are needed:
```file:path/to/file.mdx
(complete file content here)
```

You may output multiple file blocks if multiple docs need updating."""


def process_repo(repo: str, doc_files: list[str], since: str, model: str) -> dict[str, str] | None:
    """Process a single repo. Returns {filepath: new_content} or None."""
    print(f"\n--- Processing {repo} ---")

    commits = get_commits_since(repo, since)
    if not commits:
        print(f"  No commits since {since}")
        return None

    print(f"  {len(commits)} commits found")

    # Gather commit summaries and diffs (limit to avoid token overflow)
    commit_parts = []
    for c in commits[:20]:
        sha = c["sha"][:8]
        msg = c["commit"]["message"].split("\n")[0]
        diff = get_commit_diff(repo, c["sha"])
        commit_parts.append(f"### {sha}: {msg}\n```diff\n{diff}\n```")

    commits_text = "\n\n".join(commit_parts)

    # Truncate if too long
    if len(commits_text) > 50000:
        commits_text = commits_text[:50000] + "\n... (truncated)"

    # Load current docs
    docs_parts = []
    for df in doc_files:
        content = read_doc_file(df)
        if content:
            docs_parts.append(f"### {df}\n```mdx\n{content}\n```")

    if not docs_parts:
        print(f"  No existing doc files found for {repo}")
        return None

    docs_text = "\n\n".join(docs_parts)

    # Ask the LLM
    user_msg = f"""## Source Repo: {ORG}/{repo}

## Recent Commits
{commits_text}

## Current Documentation
{docs_text}

Analyze the commits and determine if any documentation needs updating."""

    print(f"  Querying {model}...")
    try:
        response = llm_complete(model, SYSTEM_PROMPT, user_msg)
    except Exception as e:
        print(f"  LLM error: {e}")
        return None

    if "NO_CHANGES" in response.strip():
        print(f"  No doc changes needed")
        return None

    # Parse file blocks from response
    updates = {}
    parts = response.split("```file:")
    for part in parts[1:]:
        lines = part.split("\n", 1)
        if len(lines) < 2:
            continue
        filepath = lines[0].strip()
        content = lines[1]
        # Strip trailing code fence
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3].rstrip()
        # Validate filepath is in our allowed list
        if filepath in doc_files:
            updates[filepath] = content
            print(f"  Will update: {filepath}")
        else:
            print(f"  Skipping disallowed path: {filepath}")

    return updates if updates else None


def main():
    if not OPENROUTER_API_KEY:
        print("OPENROUTER_API_KEY not set, exiting")
        sys.exit(1)

    state = load_state()
    now = datetime.now(timezone.utc)
    default_since = (now - timedelta(hours=LOOKBACK_HOURS)).isoformat()

    # Pick the best free model
    model = pick_best_free_model()

    all_prs = []

    for repo, doc_files in REPO_DOC_MAP.items():
        since = state.get(repo, default_since)
        updates = process_repo(repo, doc_files, since, model)

        if updates:
            timestamp = now.strftime("%Y%m%d-%H%M")
            branch = f"docs-sync/{repo}/{timestamp}"
            short_name = repo.replace("timepoint-", "")
            title = f"docs: sync {short_name} from upstream commits"
            body = (
                f"Auto-generated from recent commits to `{ORG}/{repo}`.\n\n"
                f"**Model used:** `{model}`\n\n"
                f"**Files updated:**\n"
                + "\n".join(f"- `{f}`" for f in updates)
                + "\n\n---\n*Review carefully before merging.*"
            )

            pr_url = create_pr(branch, title, body, updates)
            if pr_url:
                all_prs.append(pr_url)

        # Update state regardless (don't re-process same commits)
        state[repo] = now.isoformat()
        # Small delay to be kind to APIs
        time.sleep(1)

    save_state(state)

    if all_prs:
        print(f"\n=== Created {len(all_prs)} PR(s) ===")
        for url in all_prs:
            print(f"  {url}")
    else:
        print("\n=== No doc updates needed ===")


if __name__ == "__main__":
    main()
