# PR Copilot

AI-powered Pull Request reviewer built with **LangGraph**, **Ollama (qwen3-coder:480b-cloud)**, and the **GitHub API**.

---

## Features

| Feature | Details |
|---|---|
| **LangGraph pipeline** | Stateful, conditional, retry-capable graph |
| **Parallel static analysis** | Bandit + flake8 run in parallel fan-out |
| **Smart diff chunking** | Splits large diffs so LLM context window is never exceeded |
| **Structured LLM review** | Enforced JSON output with fallback regex extraction |
| **Inline GitHub comments** | Maps diff positions to exact file lines |
| **Retry logic** | Up to 2 LLM retries on invalid output |
| **Skip when LGTM** | Posts approval review, no noise |
| **Webhook server** | FastAPI + HMAC signature verification |
| **CLI entrypoint** | Dry-run mode, JSON output |

---

## Project Structure

```
pr_copilot/
├── __init__.py
├── config.py            ← All env-var config (singleton)
├── state.py             ← Pydantic state schema (PRCopilotState)
├── graph.py             ← LangGraph StateGraph construction + compile
├── webhook.py           ← FastAPI server (GitHub webhook receiver)
├── main.py              ← CLI entrypoint
└── nodes/
    ├── __init__.py
    ├── fetch_pr.py      ← fetch_pr_node
    ├── tools.py         ← run_bandit_node + run_flake8_node
    ├── chunker.py       ← chunk_diff_node
    ├── llm_review.py    ← llm_review_node (Ollama HTTP API)
    ├── validate.py      ← validate_output_node
    └── post_comments.py ← post_comments_node

tests/
├── test_chunker.py
├── test_validate.py
└── test_llm_review.py

requirements.txt
pyproject.toml
.env.example
CONTRIBUTING.md          ← Loaded as repo guidelines at runtime
```

---

## Setup

### 1. Clone and install

```bash
git clone <your-fork>
cd pr_copilot
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
```

### 2. Configure environment

```bash
copy .env.example .env
# Edit .env and fill in GITHUB_TOKEN, WEBHOOK_SECRET, etc.
```

### 3. Start Ollama

```bash
ollama pull qwen3-coder:480b-cloud
ollama serve
```

---

## Usage

### Webhook server (production)

```bash
# Load env vars
$env:GITHUB_TOKEN="<github-token>"     # PowerShell
# export GITHUB_TOKEN=<github-token>   # Bash

python -m pr_copilot.webhook
# → starts on http://0.0.0.0:8000
```

Point your GitHub repo webhook to `https://your-server/webhook/github`
with Content-Type `application/json` and `pull_request` events enabled.

### CLI (manual / dry-run)

```bash
# Review a PR and print to terminal (no GitHub posting)
python -m pr_copilot.main --repo owner/repo --pr 42 --dry-run

# Full review + post comments + write JSON output
python -m pr_copilot.main --repo owner/repo --pr 42 --output-json result.json
```

---

## Graph Execution Flow

```
START
  │
  ▼
fetch_pr_node
  │ (error?) → END
  ├─────────────────────────────────────┐
  ▼                                     ▼
run_bandit_node              run_flake8_node    ← PARALLEL
  │                                     │
  └──────────────┬──────────────────────┘
                 ▼
          chunk_diff_node
          (splits if > 400 lines)
                 │
                 ▼
          llm_review_node ◄──────────────────┐
          (calls Ollama per chunk)            │ retry (max 2x)
                 │                            │
                 ▼                            │
          validate_output_node ───────────────┘
          (dedup, severity summary)
                 │
          (no issues?) → post_comments_node (APPROVE)
          (issues?)    → post_comments_node (COMMENT/REQUEST_CHANGES)
                 │
                END
```

---

## State Schema

```python
class PRCopilotState(BaseModel):
    # Identity
    repo: str
    pr_number: int
    base_sha: str
    head_sha: str
    pr_title: str
    pr_body: str

    # Fetched
    files: list[PRFile]
    diffs: dict[str, str]        # filename → raw patch

    # Chunking
    diff_chunks: list[DiffChunk]
    needs_chunking: bool

    # Tool results
    bandit_results: list[BanditIssue]
    flake8_results: list[Flake8Issue]

    # LLM
    llm_reviews: list[LLMComment]
    llm_retry_count: int
    llm_raw_response: str

    # Output
    final_comments: list[LLMComment]
    review_summary: str
    has_issues: bool

    # Control
    error: Optional[str]
    skipped: bool
    repo_guidelines: str
```

---

## LLM Prompt (example)

**System:**
```
You are an expert code reviewer. You will be given a pull request diff,
static analysis results, and repository guidelines.
Your task is to produce structured, actionable review comments.

STRICT RULES:
1. Output ONLY valid JSON — no markdown fences, no prose.
2. The JSON must be an array of comment objects.
3. Each comment object must have exactly these fields:
   - "filename": string
   - "line": integer (file line number; 0 = file-level comment)
   - "position": integer (diff position for GitHub inline; 0 if unknown)
   - "severity": one of ["critical", "warning", "suggestion", "info"]
   - "category": one of ["security", "style", "architecture", "logic", "performance"]
   - "message": string (concise review comment)
   - "suggestion": string or null (concrete fix suggestion)
4. Only flag real issues — no noise, no duplicates.
5. If there are no issues, return an empty array: []
```

**User (truncated):**
```
## Pull Request Context
Title: Add user authentication endpoint

## File Under Review
src/auth.py (chunk 0, lines 1–42)

## Diff
```diff
@@ -1,3 +1,12 @@
+import os
+SECRET_KEY = "hardcoded_secret_abc123"
+password = request.form['password']
```

## Bandit Security Issues
  Line 2 [HIGH/HIGH] B105: Possible hardcoded password: 'hardcoded_secret_abc123'

## Flake8 Style Issues
  None
```

---

## Example LLM Output JSON

```json
[
  {
    "filename": "src/auth.py",
    "line": 2,
    "position": 2,
    "severity": "critical",
    "category": "security",
    "message": "Hardcoded secret key detected. This will be exposed in version control and is a critical security vulnerability.",
    "suggestion": "SECRET_KEY = os.environ['SECRET_KEY']  # Load from env or secrets manager"
  },
  {
    "filename": "src/auth.py",
    "line": 3,
    "position": 3,
    "severity": "critical",
    "category": "security",
    "message": "Raw password from form data used without validation or hashing. Use werkzeug.security.check_password_hash.",
    "suggestion": "from werkzeug.security import check_password_hash\nif not check_password_hash(user.password_hash, request.form['password']):\n    abort(401)"
  }
]
```

---

## Running Tests

```bash
pytest -v
```

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | **required** | Personal access token with `repo` scope |
| `WEBHOOK_SECRET` | `""` | HMAC secret for webhook signature verification |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen3-coder:480b-cloud` | Model name |
| `OLLAMA_TIMEOUT` | `120` | Request timeout (seconds) |
| `DIFF_CHUNK_THRESHOLD` | `400` | Lines above which chunking activates |
| `DIFF_CHUNK_SIZE` | `150` | Lines per chunk |
| `LLM_MAX_RETRIES` | `2` | Max LLM retry attempts on invalid output |
| `BANDIT_PATH` | `bandit` | Path to bandit binary |
| `FLAKE8_PATH` | `flake8` | Path to flake8 binary |
| `GUIDELINES_FILE` | `CONTRIBUTING.md` | Path to repo guidelines file |
| `PORT` | `8000` | Webhook server port |

---

## Failure Handling Strategy

| Failure | Behaviour |
|---|---|
| GitHub API error (fetch) | Sets `state.error`, graph routes to END immediately |
| Bandit/flake8 timeout | Logs warning, continues with empty results for that file |
| Ollama HTTP error | Logs error per chunk, continues with remaining chunks |
| Invalid LLM JSON output | Retry up to `LLM_MAX_RETRIES` times, then use empty comments |
| Post review error | Sets `state.error`, logged — pipeline does not crash |
| Webhook signature mismatch | Returns HTTP 401 immediately |

---

## Optional Enhancements (Extension Points)

- **Multi-agent**: Subclass the graph with separate `security_agent`, `style_agent`, `architecture_agent` nodes, each with a specialized system prompt
- **GitHub Check Runs**: Replace `post_comments_node` with a Check Run API call for richer status UI
- **Auto-fix PRs**: After LLM review, trigger a follow-up node that applies `suggestion` fields via the GitHub Contents API and opens a fixup commit
