# Buyer's Chair — Prospect Agent

A weekly agent that finds GTM people (hiring managers, enterprise AEs, and
recruiters) at 50 target frontier AI and tech companies, scores each one
against Tyler's positioning with Claude, and feeds them into the Buyer's
Chair dashboard inbox for manual review and outreach..

**Data source is Apollo.io's licensed B2B database.** The agent never
touches LinkedIn. All connection requests and messages are sent manually.
This keeps the whole system inside LinkedIn's terms of service.

## How it works

```
GitHub Actions (Mondays 6am CT)
  └─ prospect_agent.py
       ├─ Apollo API: people search per company x persona titles
       ├─ Claude API: classify (HM/AE/RC), score 1-5, one-line "why",
       │             one-line warm angle based on Tyler's background
       └─ writes prospects.json + state.json, commits to repo
             └─ Buyer's Chair dashboard (GitHub Pages) loads prospects.json
                into its Inbox: approve → person lands in the company's
                pipeline; dismiss → never shown again
```

The agent processes 15 companies per run in tier order and rotates through
the full 50 roughly every 3.5 weeks, so Apollo credits stay manageable and
the inbox refills steadily rather than flooding.

## Setup (about 15 minutes)

1. **Create the repo.** Put these files in a new GitHub repo (or the same
   repo that serves the dashboard on GitHub Pages — that's simplest, since
   the dashboard fetches `prospects.json` from its own directory):
   - `prospect_agent.py`
   - `companies.json`
   - `.github/workflows/prospect.yml`
   - `index.html` (the Buyer's Chair dashboard)

2. **Apollo account.** Sign up at apollo.io, then Settings → Integrations →
   API → create a key. Note: API access on the free tier is limited and has
   changed over time; if search calls return 403, the Basic plan (~$49/mo)
   includes API credits. Check current limits before committing.

3. **Anthropic key.** console.anthropic.com → API keys. Scoring runs cost
   pennies per week on Sonnet.

4. **Repo secrets.** GitHub repo → Settings → Secrets and variables →
   Actions → add `APOLLO_API_KEY` and `ANTHROPIC_API_KEY`.

5. **Enable GitHub Pages** (Settings → Pages → deploy from branch, root)
   so the dashboard is live and can fetch `prospects.json`.

6. **Test run.** Actions tab → "Weekly prospect run" → Run workflow.
   Check the log, then open the dashboard and hit "Load agent inbox."

## Tuning

- `companies.json` → `personas` controls which titles are searched and how
  many people per persona per company. Domains are best-guess; fix any that
  return zero results (check the Actions log).
- `run_settings.max_companies_per_run` trades Apollo credits vs. coverage
  speed.
- The scoring prompt lives in `prospect_agent.py` (`CANDIDATE_PROFILE`).
  Update it as the positioning sharpens — it directly shapes the "why" and
  "warm angle" lines in the inbox.
- Apollo's search parameter names occasionally change between API versions.
  If results come back empty for all companies, check the current docs for
  `mixed_people/search` — the likely culprit is `q_organization_domains_list`.

## Costs

- Apollo: free tier for a trial; ~$49/mo covers the full rotation comfortably
- Anthropic API: well under $1/month at this volume
- GitHub Actions + Pages: free
