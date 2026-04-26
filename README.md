# Serai

**More signal. Less noise. For people who aren't looking — yet.**

---

Last week I told seven employed people at a meetup I was building a job search tool for people who aren't looking. All seven were the target user. Two asked to see it. Here it is.

I spent 14 years at Atlassian. Six of them stuck at the same level, comfortable, not looking. I told myself the grass wasn't greener. I was wrong — I just never looked.

When I finally did look, I found every job tool was built for volume: more alerts, more applications, faster. That's fine if you need any job. It's the wrong tool if you're looking for the right one.

Serai discovers companies automatically — scanning Greenhouse, Ashby, Workday, and Y Combinator job boards for companies that match your archetype, then scoring each one against weighted dimensions you define. You don't hardcode a list of companies to watch. Serai finds them, evaluates them, and promotes the ones worth tracking. Roles at those companies are scored against your resume. The ones that pass both thresholds land in your Notion board. The rest are filtered out. Most weeks, you see nothing. That's the point.

---

## How it works

Serai runs two loops:

**Company discovery** (`run_company_discovery.py`) finds new companies you've never seen:

1. **Discovers** companies across Greenhouse, Ashby, Workday, and YC job boards using configurable search patterns
2. **Scores each company** against your candidate profile on six dimensions with your chosen weights
3. **Promotes** companies scoring above threshold into your active registry — no manual curation needed
4. **Evaluates roles** at discovered companies, filtering by title, location, and compensation
5. **Routes** each role: Apply / Network / Review / Skip — based on weighted fit + company score
6. **Writes** passing roles to your Notion board

**Recurring scan** (`eval_llm_scoring.py`) monitors your active companies every 3 hours:

1. **Scans** ATS boards for new postings at companies in your registry
2. **Scores role fit** — strength overlap, role interest, level fit, title match
3. **Grounds scores in recent signals** — Brave Search pulls the last 12 months of news, funding rounds, and leadership changes so scores reflect reality, not stale training data
4. **Routes and writes** passing roles to Notion with alert priority

Companies flow from discovery → evaluation → your board. You define what "good" looks like. Serai finds it.

## Who Serai is for

- Senior operators (PM, engineering, design, growth) with 5-15 years of experience
- People employed and not actively looking, but who don't want to miss the rare role worth moving for
- People between roles who refuse to apply to 300 postings for one offer
- Anyone who thinks job search signal-to-noise is broken

## Who Serai is not for

- Need a paycheck in 90 days? Use Simplify, Teal, or LinkedIn alerts. Serai is built for selectivity, not speed.
- Want a polished UI? Serai is a Python pipeline writing to Notion. v1 is for people who can read the code. v2 is for everyone else.

---

## Architecture

Serai separates what's universal from what's personal:

```
┌─────────────────────────────────────────────────────┐
│  Company Discovery                                  │
│  Pattern-based ATS + YC scanning, automatic         │
│  promotion of high-scoring companies                │
├─────────────────────────────────────────────────────┤
│  Generic Prompt (same for all users)                │
│  Evaluation discipline, confidence rules,           │
│  anti-bias clauses, scoring bands                   │
├─────────────────────────────────────────────────────┤
│  Candidate Profile (yours)                          │
│  Role, dimensions, weights, anchor companies,       │
│  disqualifiers, hard constraints                    │
├─────────────────────────────────────────────────────┤
│  Routing Logic (code)                               │
│  LLM scores dimensions → Python computes weighted   │
│  sums, enforces anchors, checks disqualifiers,      │
│  assigns routes                                     │
└─────────────────────────────────────────────────────┘
```

**The key design decision:** the LLM evaluates each dimension independently and returns six scores. It never computes the final score — Python does the math. This eliminates a class of errors where the model rounds toward prestige-friendly numbers or lets one dimension bleed into another.

Your candidate profile defines everything personal: which dimensions matter, how much each one weighs, what "good" looks like (anchor companies at every band), and what's an automatic skip (disqualifiers). A PM weights product culture at 0.20. A sales leader drops it and adds sales_motion. Same prompt, different profile.

### Per-dimension scoring

Serai evaluates companies on six dimensions by default. You can add, remove, or reweight dimensions by editing your candidate profile — no code changes needed.

| Dimension | What it measures | Default weight |
|---|---|---|
| Moat durability | Distribution, network effects, data, switching costs | 0.25 |
| Growth trajectory | Revenue growth, funding, market momentum | 0.20 |
| Product culture | PM ownership, product-led decision making, CPO presence | 0.20 |
| AI leverage | How effectively the company uses AI in its product | 0.15 |
| Leadership quality | Executive credibility, stability, track record | 0.10 |
| Market category | Category growth, tailwinds, TAM trajectory | 0.10 |

Each dimension is scored 1-10, calibrated against anchor companies you place at each band. A 9 means the company's evidence matches your named 9-10 anchors. A 4 means it looks like your named 3-4 anchors. The anchors define the scale — not abstract criteria.

### Anchor calibration

Anchors are the core calibration mechanism. In your candidate profile, you place real companies at score bands for each dimension:

```
moat_durability (weight: 0.25)
  9-10: elite distribution + data moat (Atlassian, Stripe)
  7-8:  strong product-led distribution (Ramp, Linear)
  5-6:  moderate moat, contestable position (Anthropic, OpenAI)
  3-4:  weak or eroding moat (ZoomInfo, Jasper)
  1-2:  no meaningful moat (Jasper)
```

The LLM uses these anchors to calibrate every score. Code-side enforcement clamps scores to anchor bands as a backstop, so even when the model drifts, named companies always land in the right range.

### Signal grounding

Company scores are supplemented by real-time web signals via Brave Search. This prevents stale training data from producing wrong scores — a company that did layoffs last month shouldn't score the same as it did a year ago.

Signal routing rules control which dimensions signals can inform. Funding news updates growth trajectory. It does not update moat durability or product culture, which require structural evidence. This prevents positive press from inflating all dimensions uniformly.

### Company discovery

You don't maintain a hardcoded list of companies. Serai discovers them automatically by scanning ATS boards (Greenhouse, Ashby, Workday) and Y Combinator's job board using configurable search patterns. Each discovered company is scored against your candidate profile. Companies that score above the promotion threshold (default 7.0) and have matching roles are automatically added to your active registry for recurring monitoring. Companies below the watchlist threshold (default 6.0) are rejected. Everything in between sits on a watchlist until stronger signal arrives.

The discovery loop also handles YC jobs end-to-end: fetch, filter by title/location/comp, score the company, score the role, route, and write to Notion — all in one pass.

### Disqualifiers

Candidate-specific disqualifiers (e.g., "consumer social / gaming", "legacy enterprise sales org") let you skip entire company categories regardless of score. The LLM flags potential matches, and code-side detection provides a backstop using known company sets and keyword matching. Disqualifiers don't affect dimensional scores — a consumer gaming company can still score well on moat and growth. The disqualifier overrides routing, not evaluation.

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/useserai/serai.git
cd serai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python 3.10+. If `python3` isn't found, check your version with `python --version` and install via [python.org](https://www.python.org/downloads/) or Homebrew (`brew install python@3.12`).

### 2. Set up API keys

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

```
MODEL_PROVIDER=openai         # openai (default), anthropic, or google
OPENAI_API_KEY=...            # if using OpenAI
ANTHROPIC_API_KEY=...         # if using Anthropic
GOOGLE_API_KEY...             # if using Google
BRAVE_SEARCH_API_KEY=...
NOTION_TOKEN=ntn_...
NOTION_DATABASE_ID=...
```

**Choosing a model provider:**

Serai supports OpenAI and Anthropic (Claude) interchangeably. Set `MODEL_PROVIDER` in your `.env` to choose:

| Provider | `MODEL_PROVIDER` | Default model | Approximate cost per company eval |
|---|---|---|---|
| OpenAI | `openai` | `gpt-4o-mini` | ~$0.01–0.02 |
| Anthropic | `anthropic` | `claude-sonnet-4-6` | ~$0.01–0.03 |
| Google Gemini | `google` | `gemini-2.5-flash` | ~$0.00–0.01 |

Override the model with `OPENAI_MODEL`, `ANTHROPIC_MODEL`, or `GOOGLE_MODEL` respectively (e.g., `GOOGLE_MODEL=gemini-2.5-flash-lite` for lower cost, `ANTHROPIC_MODEL=claude-opus-4-7` for maximum quality).

Where to get each key:

| Service | Where to get it |
|---|---|
| OpenAI | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Anthropic | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) |
| Google Gemini | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — generous free tier |
| Brave Search | [brave.com/search/api](https://brave.com/search/api/) — free tier covers typical usage |
| Notion | [notion.so/my-integrations](https://www.notion.so/my-integrations) — free plan works |

You'll set up the Notion database and get the database ID in step 4.

### 3. Configure filters

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and customize:

- Target locations (e.g., `["San Francisco, CA", "Remote"]`)
- Minimum compensation
- Target seniority levels
- Model name (controlled by `OPENAI_MODEL` or `ANTHROPIC_MODEL` in `.env`)

Each setting has comments explaining what it does.

### 4. Create your Notion database

Create a new Notion database with these properties. Names and types must match exactly — Serai writes to these fields directly.

| Property | Type | Purpose |
|---|---|---|
| Final Recommendation | Select | Route: Apply / Network / Review / Skip |
| Title | Text | Role title |
| Company | Text | Company name |
| URL | URL | Link to the job posting |
| First Seen | Date | When Serai first discovered this role |
| Location | Text | Role location |
| Comp Min | Number | Minimum base compensation |
| Comp Max | Number | Maximum base compensation |
| Apply Score | Number | Weighted fit score (computed by Serai) |
| Overall Interest | Number | LLM overall interest score |
| Company Score | Number | Weighted company dimension score |
| Role Interest | Number | LLM role interest score |
| Strength Overlap | Number | LLM strength overlap score |
| Level Fit | Number | LLM level fit score |
| Job Confidence | Number | LLM confidence in the role evaluation |
| Why Strong | Text | Top reasons this role scored well |
| Main Reservation | Text | Primary concern or risk |
| Source | Text | ATS source (e.g. greenhouse, ashby) |
| Source Job ID | Text | Job ID from the source ATS |

Then connect your Notion integration to the database:

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) and create a new integration
2. Copy the integration's API key (starts with `ntn_`) into your `.env` as `NOTION_API_KEY`
3. Open your database in Notion, click the `...` menu, click "Connections", and add your integration
4. Copy the database ID from the URL (the 32-character string after your workspace name and before the `?`) into your `.env` as `NOTION_DATABASE_ID`

### 5. Create your candidate profile

```bash
cp examples/candidate_profile.growth-pm.txt candidate_data/candidate_profile.txt
```

Open `candidate_data/candidate_profile.txt` and edit every section to match your background:

- **Role**: your target role and seniority
- **What I bring**: your actual experience and strengths
- **Dimensions and weights**: which dimensions matter to you and how much (weights must sum to 1.0)
- **Anchor companies**: real companies you place at each score band for each dimension — these calibrate the entire scoring system
- **Disqualifiers**: company categories you want to skip regardless of score
- **Hard constraints**: location, compensation, level requirements
- **Role fit guardrails**: your role family and what types of roles are not a fit

Two example profiles are included in `examples/` to show the format: one for a growth PM, one for an infrastructure engineer. They demonstrate how the same system produces different results with different profiles.

Writing good anchors takes 30-60 minutes but dramatically improves calibration. The anchors define the scale — not abstract criteria.

### 6. Add your resume

```bash
cp examples/resume.example.md candidate_data/resume.md
```

Open `candidate_data/resume.md` and replace the example content with your own resume as plain text or markdown. Serai uses this for role-level scoring — it helps the LLM assess strength overlap, level fit, and scope match against job descriptions. Format doesn't need to be perfect. Strip out personal contact info (phone, address) if you prefer — Serai doesn't need it.

### 7. Anchor stories (optional)

Anchor stories give the LLM structured examples from your career to match against job descriptions during role scoring. If provided, Serai selects the most relevant stories for each role evaluation.

```bash
cp examples/anchor_stories.example.yaml candidate_data/anchor_stories.yaml
```

Edit the file with your own stories. If you skip this step, Serai works fine without it — role scoring will just have less context about your specific experience.

### 8. Run company discovery

```bash
python run_company_discovery.py
```

This scans ATS boards, discovers companies, scores them against your profile, evaluates roles at high-scoring companies, and writes results to your Notion database. First run may take several minutes depending on how many companies are discovered.

### 9. Run the recurring role scan

```bash
python eval_llm_scoring.py
```

This checks for new roles at companies already in your active registry. Run it periodically to catch new postings.

### 10. Set up recurring runs (optional)

On macOS, use the launchd plist files in `deploy/`. Make the shell scripts executable first:

```bash
chmod +x deploy/run_discovery.sh deploy/run_eval.sh
```

Edit `deploy/run_discovery.sh` and `deploy/run_eval.sh` to set `SERAI_DIR` to wherever you cloned the repo. Then load the plist files:

```bash
cp deploy/com.serai.discovery.plist ~/Library/LaunchAgents/
cp deploy/com.serai.eval.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.serai.discovery.plist
launchctl load ~/Library/LaunchAgents/com.serai.eval.plist
```

On Linux, use `deploy/crontab.example` as a starting point for your crontab.

---

## How Serai is different from other AI job tools

Serai is not an application tool. It's a company evaluation tool.

Tools like AiApply, Sonara, and Jobright are built for volume — they help you apply to hundreds of roles faster with auto-apply, resume tailoring, and cover letter generation. They answer the question "does this role match my resume?" and they're good at it.

Serai answers a different question: "is this company the kind of place where I'd actually want to spend the next 3-5 years?" It evaluates companies on weighted dimensions, calibrated against anchor companies you define, grounded in real-time signals. No other job search tool does per-dimension company scoring with anchor calibration and signal routing.

The tools are complementary. Use Serai to find the right companies. Use whatever application tool you prefer to optimize your applications to them.

---

## Known limitations

- **Signal quality varies.** Brave Search grounding works well for well-known companies but can miss niche startups. Confidence ratings flag where evidence is thin.
- **Token usage.** Per-dimension scoring asks the LLM for six scores per company — more tokens than a single gestalt score. The tradeoff is much better calibration.
- **Profile investment.** The candidate profile requires thought. Writing good anchors is the difference between useful scores and noise. Templates in `examples/` help you get started.
- **No UI.** Serai is a Python pipeline. You interact with results through Notion and configure it through text files. This is intentional for v1.

## Roadmap

- **v1.2** Profile-driven discovery patterns (currently optimized for PM roles)
- **v1.3** Alerting (Notion + alternatives (slack / email))
- **v1.4:** `bin/generate_profile.py` — upload your resume + describe what you want, get a draft candidate profile to review and edit
- **v1.5:** Historical accuracy tracking — did companies scored 8+ actually break out?
- **v2.0:** Hosted version with resume-to-profile onboarding (if demand warrants)

---

## Credits

Built by [Alex Kassab](https://linkedin.com/in/alexandrakassab) during a job search. If Serai surfaces something interesting for you, I'd love to know.

## License

MIT
