"""
Buyer's Chair prospect agent
----------------------------
Finds GTM people (hiring managers, enterprise AEs, recruiters) at target
companies via the Apollo.io API, scores and classifies them with Claude,
and writes prospects.json for the Buyer's Chair dashboard inbox.

Data source is Apollo's licensed B2B database. This script never touches
LinkedIn itself. All outreach stays manual.

Env vars required:
  APOLLO_API_KEY      Apollo.io API key (Settings > Integrations > API)
  ANTHROPIC_API_KEY   Anthropic API key (console.anthropic.com)
"""

import hashlib
import json
import os
import sys
import time
from datetime import date, datetime

import requests

APOLLO_KEY = os.environ.get("APOLLO_API_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

APOLLO_URL = "https://api.apollo.io/api/v1/mixed_people/search"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"

CONFIG_PATH = "companies.json"
STATE_PATH = "state.json"          # rotation cursor + seen prospect ids
OUTPUT_PATH = "prospects.json"     # what the dashboard inbox loads

# Tyler's context for the scoring pass. Edit freely as positioning evolves.
CANDIDATE_PROFILE = """
Candidate: Tyler Blackwell, Chicago. Director of Global Workforce Planning at
Morningstar (public financial data company). Sits on the HR AI governance
committee; led internal deployment of Claude Code and MCP-governed retrieval.
15 years leading enterprise transformation across HR, consulting (EY,
Kincentric), and the U.S. State Department. Pivoting to GTM/AE roles at
frontier AI companies. Positioning: "the buyer they're trying to sell to" -
he has run the buyer's side of the enterprise AI sales cycle (vendor
evaluation, security review, stakeholder persuasion, procurement).
Strongest angles: financial services vertical, HR/talent tech, enterprise
AI governance, change management (Prosci certified), Chicago ties,
ex-consulting networks, builds hands-on with Claude/APIs.
"""


def die(msg):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def prospect_id(person, company_name):
    basis = (person.get("linkedin_url") or f"{person.get('name','')}|{company_name}").lower()
    return hashlib.sha1(basis.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- Apollo

def apollo_search(domain, titles, per_page):
    """One Apollo people-search call. Returns a list of person dicts."""
    payload = {
        "q_organization_domains_list": [domain],
        "person_titles": titles,
        "page": 1,
        "per_page": per_page,
    }
    resp = requests.post(
        APOLLO_URL,
        headers={"X-Api-Key": APOLLO_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if resp.status_code == 429:
        print("  Apollo rate limit hit; sleeping 60s")
        time.sleep(60)
        return apollo_search(domain, titles, per_page)
    if resp.status_code != 200:
        print(f"  Apollo error {resp.status_code} for {domain}: {resp.text[:200]}")
        return []
    people = resp.json().get("people", [])
    out = []
    for p in people:
        name = p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        if not name:
            continue
        out.append({
            "name": name,
            "title": p.get("title") or "",
            "linkedin_url": p.get("linkedin_url") or "",
            "location": ", ".join(filter(None, [p.get("city"), p.get("state")])),
            "headline": p.get("headline") or "",
        })
    return out


# ---------------------------------------------------------------- Claude scoring

def claude_score(company, candidates):
    """Classify and score a batch of people for one company. Returns dict by index."""
    if not candidates:
        return {}
    roster = json.dumps(
        [{"i": i, "name": c["name"], "title": c["title"], "headline": c["headline"],
          "location": c["location"], "persona_hint": c["persona"]}
         for i, c in enumerate(candidates)],
        indent=1,
    )
    prompt = f"""{CANDIDATE_PROFILE}

Target company: {company['name']} (tier {company['tier']}, vertical focus: {company['vertical'] or 'general'}).

Below is a list of people found at this company. For each, assess fit as an
outreach target for the candidate's GTM job search.

Personas: HM = hiring manager (VP/Head of Sales, CRO, GTM leadership),
AE = current enterprise seller (intel conversations), RC = recruiter.

People:
{roster}

Return ONLY a JSON array, no prose, no markdown fences. One object per person:
{{"i": <index>, "persona": "HM"|"AE"|"RC"|"SKIP", "score": 1-5, "why": "<one line: why this person is worth contacting>", "warm_angle": "<one line: the most specific hook Tyler has with this person, or empty string>"}}

Use SKIP for people who don't fit any persona (wrong function, too junior,
clearly wrong team). Score 5 = ideal target, 1 = marginal. Be selective:
a shorter high-quality list beats a long padded one."""

    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    if resp.status_code != 200:
        print(f"  Claude error {resp.status_code}: {resp.text[:200]}")
        return {}
    text = "".join(b.get("text", "") for b in resp.json().get("content", []))
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        rows = json.loads(text)
        return {r["i"]: r for r in rows if isinstance(r, dict) and "i" in r}
    except (json.JSONDecodeError, TypeError) as e:
        print(f"  Could not parse Claude response: {e}")
        return {}


# ---------------------------------------------------------------- main

def main():
    if not APOLLO_KEY:
        die("APOLLO_API_KEY is not set")
    if not ANTHROPIC_KEY:
        die("ANTHROPIC_API_KEY is not set")

    config = load_json(CONFIG_PATH, None) or die("companies.json missing")
    state = load_json(STATE_PATH, {"cursor": 0, "seen": []})
    output = load_json(OUTPUT_PATH, {"generated": "", "prospects": []})
    seen = set(state.get("seen", []))

    companies = config["companies"]
    personas = config["personas"]
    batch_size = config["run_settings"]["max_companies_per_run"]

    cursor = state.get("cursor", 0) % len(companies)
    batch = [companies[(cursor + k) % len(companies)] for k in range(batch_size)]
    print(f"Run {date.today()}: companies {cursor} through {(cursor + batch_size - 1) % len(companies)}")

    new_prospects = []
    for co in batch:
        print(f"\n{co['name']} ({co['domain']})")
        found = []
        for pkey, pconf in personas.items():
            people = apollo_search(co["domain"], pconf["titles"], pconf["per_company"])
            for person in people:
                person["persona"] = pkey
            found.extend(people)
            time.sleep(1.5)  # be polite to the API

        # dedupe within company by linkedin_url/name, drop already-seen
        uniq, keys = [], set()
        for person in found:
            pid = prospect_id(person, co["name"])
            if pid in seen or pid in keys:
                continue
            keys.add(pid)
            person["_id"] = pid
            uniq.append(person)
        print(f"  {len(found)} found, {len(uniq)} new")
        if not uniq:
            continue

        scores = claude_score(co, uniq)
        kept = 0
        for i, person in enumerate(uniq):
            s = scores.get(i)
            if not s or s.get("persona") == "SKIP":
                continue
            new_prospects.append({
                "id": person["_id"],
                "company": co["name"],
                "name": person["name"],
                "title": person["title"],
                "linkedin_url": person["linkedin_url"],
                "location": person["location"],
                "persona": s.get("persona", person["persona"]),
                "score": s.get("score", 3),
                "why": s.get("why", ""),
                "warm_angle": s.get("warm_angle", ""),
                "found": str(date.today()),
            })
            seen.add(person["_id"])
            kept += 1
        print(f"  {kept} kept after scoring")

    # merge: keep existing unreviewed prospects, append new, sort by score
    merged = output.get("prospects", []) + new_prospects
    merged.sort(key=lambda p: (-p.get("score", 0), p.get("company", "")))

    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "generated": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "prospects": merged,
        }, f, indent=1)

    with open(STATE_PATH, "w") as f:
        json.dump({
            "cursor": (cursor + batch_size) % len(companies),
            "seen": sorted(seen),
        }, f, indent=1)

    print(f"\nDone. {len(new_prospects)} new prospects, {len(merged)} total in inbox.")


if __name__ == "__main__":
    main()
