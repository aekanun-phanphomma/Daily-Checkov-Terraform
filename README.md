# CheckovDaily

One Terraform misconfiguration a day, posted to LinkedIn and a Facebook Page:
what the Checkov rule checks, why it matters, a diagram, and the misconfigured
and fixed Terraform. An LLM writes it, but **real tools decide whether it is
published**: the misconfigured example must FAIL the rule in Checkov, the fix
must PASS it, and both must pass `terraform validate`. If they do not, nothing
is posted.

```text
GitHub Actions  (cron 30 0 * * *  ==  07:30 Asia/Bangkok)
│
├─ generate job  (Gemini key only, read-only repo, no social tokens)
│   ├─ select rule        catalog/rules.yaml minus state/posted.json,
│   │                     P1 first, rotating AWS -> Azure -> GCP
│   ├─ LLM: content       bad_tf, good_tf, why_it_matters, post_en
│   │    G1 schema        jsonschema; "UNKNOWN" -> skip rule
│   │    G6 post text     plain English, no URLs/hashtags/emoji/code
│   │    G2 safety        HCL parses; no module/provider/provisioner/
│   │                     external data; only aws_/azurerm_/google_ types
│   │    G3 terraform     init -backend=false + validate (temp copy), fmt
│   │    G4 bad FAILS     checkov --check <ID>  -> in failed_checks
│   │    G5 good PASSES   <ID> passed + no P1 sibling failed + guideline URL
│   ├─ LLM: diagram spec  nodes/edges/code_diff as JSON
│   │    G7 diagram       ids consistent, code_diff copied from the code
│   │    G8 render        HTML template -> 9 s MP4 + PNG (1200x1500), must fit
│   │   any gate fails -> repair prompt with the tool output
│   │   (2 repairs per rule, shared) -> next rule (max 3 per run)
│   └─ upload bundle  |  on failure: open a GitHub issue
│
└─ publish job  (social tokens, contents: write, never runs model output)
    ├─ stage: out/today -> out/<date>/, add state entry, commit + push
    ├─ post LinkedIn (video, PNG fallback), then Facebook Page (/videos, /photos fallback)
    │   each id saved to state/posted.json the moment it is confirmed
    └─ commit state (even if a platform failed, so re-runs never duplicate)
```

Every post links to its `out/<date>/` folder, so anyone can clone the repo
and re-run the proof: `checkov -d out/<date>/bad --check <ID>`.

## Design decisions

- **The LLM drafts and the tools judge.** The model never decides that its
  own output is correct. It also gets the check's real source code (resolved
  from `checkov --list`) as context, so it does not have to guess what the
  check inspects. That is the single biggest cut in repair loops.
- **Priority is ours, not Checkov's.** Open-source Checkov has no severity
  (severity comes from Prisma Cloud and needs an API key). `P1`–`P3` in the
  catalog are a curator's call, and posts never claim "Checkov severity".
- **Every link comes from a tool.** The model is forbidden from writing URLs
  (gate G6). The post's links are the repo folder we just pushed, the check's
  source from `checkov --list` and the guideline URL from Checkov's own JSON
  report. A rule without a guideline is skipped.
- **No text-to-image models.** They misspell labels, draw arrows the wrong
  way and cannot be verified. The model writes a small JSON spec, G7 checks
  it against the verified code, and a fixed template renders it -- as a
  9-second MP4 where traffic visibly reaches the resource before the fix and
  stops at a guard after it. Frames are seeked, not screen-recorded, so the
  same spec always yields the same video. The MP4 is not committed (~150 MB
  a year); the PNG is, and is the fallback if a video upload fails.
- **Model output is untrusted code.** `terraform init` downloads whatever the
  configuration asks for, so G2 rejects modules, third-party providers,
  provisioners and external data *before* init runs. The job that runs it
  holds no social tokens and cannot push.
- **Retry budget per rule, not per gate.** A fix for one gate can break
  another, so every repair re-runs all gates, and a shared budget of 2 keeps
  the worst case at 3 rules × 3 attempts. A rule that still fails is skipped
  for the next one. Model unavailability stops the run (not the rule's fault).
- **Proof before post, state per platform.** The bundle is pushed before
  posting so the link works immediately. LinkedIn and Facebook ids are
  recorded separately, so re-running after a partial failure only posts to
  the platform that is missing.
- **Gemini, Flash first.** Same key and fallback/retry logic as
  Daily-news-IT-Guy, but Flash before Flash-Lite: here the model writes code
  that must survive validate + Checkov, and each failed attempt costs another
  call plus a minute of tooling. See `src/llm.py`.

## Setup

### 1. Secrets (same names and values as Daily-news-IT-Guy)

Secrets cannot be copied between repositories, so set them again here.
LinkedIn and Facebook tokens can be shared by both bots.

```bash
gh secret set GEMINI_API_KEY        # https://aistudio.google.com/apikey
gh secret set LINKEDIN_ACCESS_TOKEN # scope: openid profile w_member_social
gh secret set LINKEDIN_AUTHOR_URN   # urn:li:person:<id>  (optional, skips /userinfo)
gh secret set FACEBOOK_PAGE_ID
gh secret set FACEBOOK_PAGE_TOKEN   # Page token, not a user token
```

A platform without its secrets is skipped, not failed. Setting only half of
the Facebook pair is an error.

### 2. Variables

```bash
gh variable set LINKEDIN_TOKEN_ISSUED --body 2026-10-16   # day the token was issued
# optional overrides, no commit needed:
gh variable set GEMINI_MODEL     --body "gemini-3.7-flash,gemini-3.5-flash"
gh variable set LINKEDIN_VERSION --body 202601
```

`linkedin-token-check` opens an issue when the token has less than 10 days
left. It expires 60 days after issue and cannot be refreshed automatically.

### 3. First run

Actions → **checkov-daily** → Run workflow → tick **dry run**. It generates,
runs every gate, uploads the bundle as an artifact and prints the post,
without committing or posting anything. Untick it to publish for real.

## Local development

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv -r requirements.txt
.venv/Scripts/python -m playwright install chromium     # Linux/macOS: .venv/bin/python

python -m pytest -q                       # fast tests incl. catalog drift
RUN_SLOW=1 python -m pytest -q            # + end-to-end on the S3 fixture

# whole pipeline without an API key, replaying a recorded answer
LLM_FIXTURE_DIR=tests/fixtures/s3 python src/generate.py --rule CKV_AWS_53
python src/publish.py post --dry-run --dir out/today
python src/select_rule.py                 # preview the queue
```

## Operating it

| Event | What happens | What to do |
| --- | --- | --- |
| No rule passed today | Issue "no post for <date>" with each rule's failing gate | If one rule keeps failing, set `enabled: false` on it |
| LinkedIn 401 | Job fails, Facebook still posts, re-run posts LinkedIn only | Refresh the token, update secret + `LINKEDIN_TOKEN_ISSUED` |
| LinkedIn 426 | Version retired | Set `LINKEDIN_VERSION` to a current `YYYYMM` |
| Bump Checkov | `tests/test_catalog.py` flags IDs that moved | Fix the catalog, then bump |
| Catalog exhausted | generate exits 1 → issue | Add rules (verify with `checkov --list`) |

## Layout

```text
catalog/rules.yaml        curated rules (verified against pinned Checkov)
state/posted.json         history, one entry per day, ids per platform
prompts/                  system, content, diagram, repair
schemas/                  content + diagram JSON schemas
src/select_rule.py        catalog + state -> ordered candidates
src/generate.py           LLM calls, repair loop, rule fallback
src/gates.py              G1-G7
src/render.py             G8: spec -> HTML -> PNG (Playwright)
src/publish.py            stage, LinkedIn, Facebook
templates/diagram.html
tests/                    unit tests + S3 fixture end-to-end
out/<date>/               published bundles: bad/ good/ card.png post.txt meta.json
```
