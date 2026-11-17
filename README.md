# job-finder

A small command-line tool that searches public job boards for **remote data roles** (data analyst,
data scientist, data/analytics engineer, ML/AI engineer, BI) and ranks them against **your own profile**:
skills, seniority, salary floor, location eligibility and language.

It produces a ranked report (Markdown + CSV) so you can spend your time reading and applying to the
postings that actually fit, instead of scrolling through hundreds that don't.

> **It never applies to anything.** The tool only reads public job APIs. Applying stays a manual,
> deliberate decision.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [What it does](#what-it-does)
- [What it does not do](#what-it-does-not-do)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [What to expect](#what-to-expect)
- [How a posting is scored](#how-a-posting-is-scored)
- [Sources](#sources)
- [Privacy: what is never committed](#privacy-what-is-never-committed)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [Tests](#tests)

---

## Why this exists

Remote job boards mix thousands of postings: senior-only roles, "US only" positions, jobs paying far below
your expectations, or roles requiring a level of English you don't have. Filtering that by hand every week
is slow and error-prone.

`job-finder` automates the boring part: it collects postings from several sources, discards what clearly
does not fit, and scores the rest so the best matches are at the top.

## What it does

1. **Collects** postings from public job-board APIs and from the public career pages (ATS) of selected
   companies.
2. **Filters out** postings that are:
   - not data roles,
   - above your seniority (Senior, Lead, Staff, Principal, Manager...) or require too many years of
     experience,
   - not remote, or restricted to regions you cannot work from (e.g. "US only"),
   - older than a configurable number of days,
   - paying below your salary floor (when the salary is published).
3. **Scores** the remaining ones from 0 to 100 based on skill match, seniority fit, salary, location and
   language.
4. **Writes** a ranked report to `output/`, plus a summary of why postings were rejected.

## What it does not do

- It does **not** submit applications, fill forms, or log into any site.
- It does **not** scrape LinkedIn/Indeed by default (that integration exists but is disabled, see
  [Sources](#sources)).
- It does **not** send your CV or profile anywhere. Your profile lives only in a local config file and
  is used only to filter results on your machine.
- It does **not** guarantee a posting is legitimate. Always verify the company before sharing personal data.

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10 or newer | Tested with 3.11 |
| pip | any recent | To install dependencies |
| Internet access | — | Only outgoing HTTPS GET requests to the sources listed below |
| Git | optional | Only if you version the project |

Python dependencies (see `requirements.txt`): `requests`, `PyYAML`, and `pytest` for the tests.
No API keys or accounts are needed.

## Installation

```bash
# 1. Get the code
git clone <your-repo-url> job-finder
cd job-finder

# 2. (Recommended) create a virtual environment
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your private configuration
# Windows (PowerShell)
Copy-Item config.example.yaml config.yaml
# macOS / Linux
cp config.example.yaml config.yaml
```

Then edit `config.yaml` with your own profile (next section).

## Configuration

All your personal criteria live in `config.yaml`, which is **git-ignored**. `config.example.yaml` is the
committed template and contains no personal data. All patterns are case-insensitive Python regular
expressions.

| Section | What you set | Example |
|---|---|---|
| `profile.skills` | Your skills and how much each one matters (weight) | `python: 3`, `power bi: 3`, `llms?: 2` |
| `filters.title_include` / `title_exclude` | Which job titles count as data roles | `'\bdata\b'`, `'analytics'` |
| `filters.seniority` | Levels you accept / reject, max years of experience required | accept `junior`, `semi senior`; reject `senior`, `lead` |
| `filters.location` | Regions you can legally work from, preferred regions, blocked phrases | allowed `latam`, `worldwide`; blocked `US only` |
| `filters.salary` | Monthly USD floor, target, approximate FX rates | `min_monthly_usd: 3000` |
| `filters.language` | Phrases that signal demanding English, and whether to reject or just penalize | `fluent english`, `\bC1\b` |
| `scoring` | Relative weight of each scoring dimension | `skills: 40` |
| `sources` | Which sources are enabled and their parameters (categories, company slugs...) | `greenhouse: {companies: [gitlab]}` |
| `output` | Report folder, how many rows in the Markdown report, minimum score | `top_n: 40` |

Tips:

- **Salary** is compared in USD per month. Yearly salaries are divided by 12, hourly ones multiplied by
  `hours_per_month`. Other currencies use the approximate rates in `fx_to_usd`; update them occasionally.
- **"Semi Senior" is mid level.** The example patterns are written so it is not rejected as "Senior".
- **Adding companies:** pick remote-friendly companies from
  [remoteintech/remote-jobs](https://github.com/remoteintech/remote-jobs) or
  [matildaexp/remote-latam](https://github.com/matildaexp/remote-latam), find their careers page URL and
  add the slug to the matching ATS:
  - `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>` → `sources.greenhouse.companies`
  - `jobs.lever.co/<slug>` → `sources.lever.companies`
  - `jobs.ashbyhq.com/<slug>` → `sources.ashby.companies`

## Usage

```bash
python -m jobfinder                         # uses ./config.yaml
python -m jobfinder --config other.yaml     # use another config file
python -m jobfinder -v                      # verbose logging
```

On Windows, if accented characters look broken in the console, run
`$env:PYTHONIOENCODING = "utf-8"` first. The report files themselves are always UTF-8.

## What to expect

- **Run time:** roughly 1 to 3 minutes, depending on how many sources and companies are enabled. The tool
  waits a little between requests to be polite with free APIs and retries transient errors.
- **Console output:** how many postings each source returned, then the totals and the report paths:

  ```text
  remotive       32 postings
  jobicy         78 postings
  himalayas     360 postings
  ...
  1762 fetched, 8 kept. Reports:
    output/jobs-20261117-1602.csv
    output/jobs-20261117-1602.md
  ```

- **Reports** in `output/`:
  - `jobs-<date>.md`: the top matches as a table (score, role, company, location, USD/month, flags, link)
    and a breakdown of rejection reasons.
  - `jobs-<date>.csv`: every kept posting with matched skills, source and publication date. Opens in Excel
    (UTF-8 with BOM).
- **Flags** in the report:

  | Flag | Meaning |
  |---|---|
  | `latam-friendly` | Location explicitly names one of your preferred regions |
  | `junior/mid` | Title or source level matches an accepted seniority |
  | `spanish` | Posting is written in Spanish |
  | `english-demanding` | Asks for fluent/native/C1+ English (penalized, not removed) |
  | `salary-unknown` | No salary published; kept so you can ask |
  | `ask-for-more` | Published range goes above your target: negotiate up |

- **Few results is normal.** Most remote data postings are senior or region-restricted. The
  "Rejected by reason" section tells you which filter removes the most, so you can decide whether to
  relax it (for example `max_years_required` or `max_age_days`) or add more companies.
- **One source failing does not stop the run.** It is logged as a warning and the rest continue.

## How a posting is scored

Postings go through the filters in this order; the first one that fails rejects the posting:

1. Data role (`title_include` / `title_exclude`).
2. Seniority (`reject_title`, `reject_hint`, `max_years_required`).
3. Location (remote, allowed regions, blocked phrases).
4. Freshness (`max_age_days`).
5. Salary floor (only if a salary is published).
6. Language (only rejects if `reject_english_demanding: true`).

Survivors get a score from 0 to 100:

| Dimension | Full points when | Partial points when |
|---|---|---|
| Skills | The posting mentions enough of your weighted skills (`match_saturation`) | Proportional to matched skills |
| Seniority | Title/level explicitly matches an accepted level | Level not stated |
| Salary | Published and above the floor | Not published |
| Location | Preferred region named | Other allowed or neutral location |
| Language | Spanish posting | English without demanding requirements (lower if demanding) |

Duplicates (same company and title from different sources) are merged, keeping the best score.
Postings below `output.min_score` are not shown.

## Sources

| Source | Endpoint | Notes |
|---|---|---|
| Remotive | `remotive.com/api/remote-jobs` | Public API, filter by category |
| Jobicy | `jobicy.com/api/v2/remote-jobs` | Supports `geo=latam` / `geo=argentina` |
| Himalayas | `himalayas.app/jobs/api/search` | 20 results per page, paginated |
| RemoteOK | `remoteok.com/api` | Attribution required if you republish data |
| Get on Board | `getonbrd.com/api/v0` | LATAM-focused, many postings in Spanish |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<slug>/jobs` | Official public job-board API |
| Lever | `api.lever.co/v0/postings/<slug>` | Official public postings API |
| Ashby | `api.ashbyhq.com/posting-api/job-board/<slug>` | Official public job-board API |
| JobSpy (**disabled**) | `python-jobspy` package | Scrapes LinkedIn/Indeed; may violate their terms and get your IP blocked |

To enable JobSpy, install it explicitly with `pip install python-jobspy` (**not** `jobspy`, which is an
unrelated package) and set `sources.jobspy.enabled: true`.

### Job boards queried

| Board | Focus | Parameters used by default |
|---|---|---|
| [Remotive](https://remotive.com) | Remote jobs worldwide | categories `data`, `artificial-intelligence` |
| [Jobicy](https://jobicy.com) | Remote jobs, filterable by region | industry `data-science`; geos: any, `latam`, `argentina` |
| [Himalayas](https://himalayas.app) | Remote jobs with salary and seniority data | queries: data analyst, data scientist, data engineer, machine learning, analytics engineer, business intelligence |
| [RemoteOK](https://remoteok.com) | Remote jobs worldwide | tags `data`, `analyst`, `machine-learning` |
| [Get on Board](https://www.getonbrd.com) | LATAM tech jobs, many in Spanish | categories `data-science-analytics`, `machine-learning-ai` |

### Company career portals (ATS)

These slugs were checked against the public APIs at the time of writing. Companies change ATS or close
boards, so a slug that starts returning 404 should be removed or updated.

| Company | ATS | Slug | Careers page |
|---|---|---|---|
| GitLab | Greenhouse | `gitlab` | https://job-boards.greenhouse.io/gitlab |
| Wizeline | Greenhouse | `wizeline` | https://job-boards.greenhouse.io/wizeline |
| Canonical | Greenhouse | `canonical` | https://job-boards.greenhouse.io/canonical |
| Grafana Labs | Greenhouse | `grafanalabs` | https://job-boards.greenhouse.io/grafanalabs |
| Toptal | Lever | `toptal` | https://jobs.lever.co/toptal |
| Supabase | Ashby | `supabase` | https://jobs.ashbyhq.com/supabase |
| PostHog | Ashby | `posthog` | https://jobs.ashbyhq.com/posthog |
| n8n | Ashby | `n8n` | https://jobs.ashbyhq.com/n8n |
| Airbyte | Ashby | `airbyte` | https://jobs.ashbyhq.com/airbyte |

Other slugs that responded but are mostly region-restricted (US/EU) and therefore not enabled by default:
Greenhouse `datadog`, `cloudflare`, `reddit`, `airtable`; Lever `palantir`, `binance`; Ashby `ramp`, `linear`.

### Where to find more companies

Curated lists to grow the company list. They are plain Markdown lists; nothing needs to be installed.

| Resource | What it contains |
|---|---|
| [lukasz-madon/awesome-remote-job](https://github.com/lukasz-madon/awesome-remote-job) | Large curated list of remote job boards, companies and resources |
| [remoteintech/remote-jobs](https://github.com/remoteintech/remote-jobs) | Remote-friendly tech companies, with their regions (also at remoteintech.company) |
| [matildaexp/remote-latam](https://github.com/matildaexp/remote-latam) | Companies that hire remotely from Latin America |
| [emredurukn/awesome-job-boards](https://github.com/emredurukn/awesome-job-boards) | Job boards grouped by niche |

### Other LATAM boards (manual search, no API used)

| Board | Notes |
|---|---|
| [Torre](https://torre.ai) | LATAM remote network; independent reviews are scarce, verify each posting |
| [Get on Board](https://www.getonbrd.com) | Also queried by the tool; free for candidates |
| [Jobicy, Argentina](https://jobicy.com/job-region/argentina) | Also queried by the tool via `geo=argentina` |
| [DailyRemote, Argentina](https://dailyremote.com/remote-jobs-in-argentina) | Free browsing; promotes a paid Premium plan you do not need |

### Third-party tools reviewed and not integrated

| Tool | Status | Reason |
|---|---|---|
| [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops) | Use with caution | AI pipeline for Claude Code; its batch mode runs the agent with permission bypass on untrusted job text |
| [srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher) | Use with caution | Sends your CV to OpenAI by default; local only when configured with Ollama |
| [Gsync/jobsync](https://github.com/Gsync/jobsync) | Use with caution | Self-hosted tracker; Docker defaults use placeholder secrets and expose the port to the LAN |
| [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) | Optional, disabled | Scraping LinkedIn/Indeed may violate their terms |
| Auto-apply bots (e.g. ApplyPilot) | Not used | Applying stays manual by design |

## Privacy: what is never committed

Everything derived from you stays local. `.gitignore` excludes:

| Ignored | Why |
|---|---|
| `config.yaml`, `config.*.yaml` (except the example), `*.local.yaml` | Your skills, salary, seniority and location criteria |
| `.env`, `.env.*` | Any future secrets |
| `output/`, `*.csv` | Reports are job lists built from your profile |
| `*.pdf`, `*.doc`, `*.docx`, `cv/`, `resume/` | CVs and personal documents |

Before your first commit, check it with:

```bash
git status --ignored
git check-ignore -v config.yaml output/
```

If you ever committed one of these by mistake, `git rm --cached <file>` removes it from the index, but it
stays in history: rewrite history or treat the data as exposed.

## Security notes

- Only HTTPS `GET` requests to the sources above; no logins, cookies or API keys.
- The config is read with `yaml.safe_load` (no code execution from YAML).
- Posting descriptions are treated as plain text: HTML is stripped and nothing from a posting is executed.
- If you feed the reports into an AI agent, remember that posting text is **untrusted input** and can
  contain instructions aimed at the agent. Do not run such agents with permission bypasses.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Config not found: config.yaml` | Copy `config.example.yaml` to `config.yaml` |
| `<source> failed: Read timed out` | Temporary slowness; it is retried automatically. Raise `http.timeout` if it persists |
| `<source> failed: 404` for a company | Wrong ATS slug, or the company moved to another ATS |
| Very few results | Check "Rejected by reason" in the report and relax the dominant filter |
| Broken accents in console | Set `PYTHONIOENCODING=utf-8`; report files are fine |

## Project structure

```text
job-finder/
├── jobfinder/
│   ├── models.py        # Job dataclass
│   ├── sources.py       # one fetcher per source (read-only HTTP)
│   ├── scoring.py       # pure filtering and scoring rules
│   ├── cli.py           # orchestration and report writing
│   └── __main__.py      # python -m jobfinder
├── tests/
│   └── test_scoring.py  # unit tests for the rules
├── config.example.yaml  # committed template, no personal data
├── config.yaml          # YOUR profile (git-ignored)
├── output/              # generated reports (git-ignored)
├── requirements.txt
└── README.md
```

## Tests

```bash
python -m pytest
```

The tests cover salary parsing and normalization, seniority rules (including "Semi Senior"), location
restrictions, years of experience, language detection and freshness. They use `config.example.yaml`, so
they never depend on your private config.
