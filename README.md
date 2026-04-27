# Serai

**More signal. Less noise. For people who aren't looking — yet.**

---

Last week I told seven employed people at a meetup I was building a job search tool for people who aren't looking. All seven were the target user. Two asked to see it. Here it is.

I spent 14 years at Atlassian. Six of them stuck at the same level, comfortable, not looking. I told myself the grass wasn't greener. I was wrong — I just never looked.

When I finally did look, I found every job tool was built for volume: more alerts, more applications, faster. That's fine if you need any job. It's the wrong tool if you're looking for the right one.

Serai discovers companies automatically — scanning Greenhouse, Ashby, Workday, and Y Combinator job boards for companies that match your archetype, then scoring each one against weighted dimensions you define. You don't hardcode a list of companies to watch. Serai finds them, evaluates them, and promotes the ones worth tracking. Roles at those companies are scored against your resume with a two-stage evaluation funnel adapted from career-ops methodology. The ones that pass both thresholds land in your Notion board. The rest are filtered out. Most weeks, you see nothing. That's the point.

---

## How it works

Serai runs two loops:

**Company discovery** (`run_company_discovery.py`) finds new companies you've never seen:

1. **Discovers** companies across Greenhouse, Ashby, Workday, and YC job boards using configurable search patterns
2. **Scores each company** against your candidate profile on six dimensions with your chosen weights
3. **Promotes** companies scoring above threshold into your active registry — no manual curation needed
4. **Evaluates roles** at discovered companies using a two-stage funnel adapted from career-ops
5. **Writes** passing roles to your Notion board

**Recurring scan** (`eval_llm_scoring.py`) monitors your active companies every 3 hours:

1. **Scans** ATS boards for new postings at companies in your registry
2. **Stage 1 (screen)**: Runs blocks A+B+C — archetype classification, resume match, level fit. Routes: Apply (4.0+), Apply with Caution (3.5–3.9), Skip (<3.5)
3. **Stage 2 (deep eval)**: Only roles passing Stage 1 enter blocks D+E+F+G — comp & market demand, resume personalization, interview preparation, posting legitimacy. Final routes: Strong Apply, Apply, Do Not Apply
4. **Grounds scores in recent signals** — Brave Search pulls the last 12 months of news, funding rounds, and leadership changes so scores reflect reality, not stale training data
5. **Writes** passing roles to Notion with alert priority

Companies flow from discovery → evaluation → your board. You define what good looks like. Serai finds it.

## Who Serai is for

- Senior operators (PM, engineering, design, growth) with 5–15 years of experience
- People employed and not actively looking, but who don't want to miss the rare role worth moving for
- People between roles who refuse to apply to 300 postings for one offer
- Anyone who thinks job search signal-to-noise is broken

## Who Serai is not for

- Need a paycheck in 90 days? Use Simplify, Teal, or LinkedIn alerts. Serai is built for selectivity, not speed
- Want a polished UI? Serai is a Python pipeline writing to Notion. v1 is for people who can read the code. v2 is for everyone else

---

## Architecture

Serai separates what's universal from what's personal:

```
┌─────────────────────────────────────────────────────┐
│  Company Discovery                                  │
│  Pattern-based ATS + YC scanning, automatic         │
│  promotion of high-scoring companies                │
├─────────────────────────────────────────────────────┤
│  Role Evaluation (Two-stage funnel)                 │
│  Stage 1: Screens on resume fit + level             │
│  Stage 2: Deep eval for comp, personalization, news │
├─────────────────────────────────────────────────────┤
│  Generic Prompts (same for all users)               │
│  Evaluation discipline, confidence rules,           │
│  anti-bias clauses, scoring bands                   │
├─────────────────────────────────────────────────────┤
│  Candidate Profile (yours)                          │
│  Config: roles, dimensions, weights, filters        │
│  Resume: plain text or markdown                     │
│  Profile: prose scoring guidance (optional)         │
├─────────────────────────────────────────────────────┤
│  Routing Logic (code)                               │
│  LLM assigns routes → Python enforces rules,        │
│  checks disqualifiers, routes to Notion             │
└─────────────────────────────────────────────────────┘
```

**The key design decision:** the LLM evaluates each dimension independently and returns six scores. Python computes the final weighted sum, enforces anchor bands, checks disqualifiers, and assigns routes. This eliminates a class of errors where the model rounds toward prestige-friendly numbers or lets one dimension bleed into another.

Your candidate profile defines everything personal: which dimensions matter, how much each one weighs, what good looks like (anchor companies at every band), and what's an automatic skip (disqualifiers). A PM weights product culture at 0.20. A data engineer weights engineering depth higher. Same prompt, different profile.

### Per-dimension scoring

Serai evaluates companies on six dimensions by default. You can add, remove, or reweight dimensions by editing your config — no code changes needed.

| Dimension | What it measures | Default weight |
|---|---|---|
| Moat durability | Distribution, network effects, data, switching costs | 0.25 |
| Growth trajectory | Revenue growth, funding, market momentum | 0.20 |
| Product culture | PM ownership, product-led decision making, CPO presence | 0.20 |
| AI leverage | How effectively the company uses AI in its product | 0.15 |
| Leadership quality | Executive credibility, stability, track record | 0.10 |
| Market category | Category growth, tailwinds, TAM trajectory | 0.10 |

Each dimension is scored 1–10, calibrated against anchor companies you place at each band. A 9 means the company's evidence matches your named 9–10 anchors. A 4 means it looks like your named 3–4 anchors. The anchors define the scale — not abstract criteria.

### Anchor calibration

Anchors are the core calibration mechanism. In your config, you place real companies at score bands for each dimension:

```
moat_durability (weight: 0.25)
  9-10: elite distribution + data moat (Atlassian, Stripe)
  7-8:  strong product-led distribution (Ramp, Linear)
  5-6:  moderate moat, contestable position (Anthropic, OpenAI)
  3-4:  weak or eroding moat (ZoomInfo, Jasper)
  1-2:  no meaningful moat
```

The LLM uses these anchors to calibrate every score. Code-side enforcement clamps scores to anchor bands as a backstop, so named companies always land in the right range.

### Signal grounding

Company scores are supplemented by real-time web signals via Brave Search. This prevents stale training data from producing wrong scores. A company that did layoffs last month shouldn't score the same as it did a year ago.

Signal routing rules control which dimensions signals can inform. Funding news updates growth trajectory. It does not update product culture or moat durability, which require structural evidence. This prevents positive press from inflating all dimensions uniformly.

### Company discovery

You don't maintain a hardcoded list of companies. Serai discovers them automatically by scanning ATS boards (Greenhouse, Ashby, Workday) and Y Combinator's job board using configurable search patterns. Each discovered company is scored against your candidate profile. Companies that score above the promotion threshold (default 7.0) and have matching roles are automatically added to your active registry for recurring monitoring.

The discovery loop also handles YC jobs end-to-end: fetch, filter by title/location/comp, score the company, score the role, and write to Notion.

### Two-stage role evaluation

When a role is discovered or rescanned, it flows through two stages:

**Stage 1 (screen):** Blocks A+B+C — archetype classification, resume match, level fit. Runs on all roles. Routes: Apply (4.0+), Apply with Caution (3.5–3.9), Skip (<3.5). Fast, lightweight.

**Stage 2 (deep eval):** Blocks D+E+F+G — comp & market demand, resume personalization plan, interview preparation (STAR+R stories), posting legitimacy. Only roles passing Stage 1 enter Stage 2. More expensive but only on promising roles. Routes: Strong Apply, Apply, Do Not Apply. Do Not Apply roles never reach your Notion board.

### Disqualifiers

Candidate-specific disqualifiers (e.g., consultancy, legacy enterprise, consumer social) let you skip entire company categories regardless of score. The LLM flags potential matches, and code-side detection provides a backstop using keyword matching. Disqualifiers don't affect dimensional scores — a consumer gaming company can still score well on moat and growth. The disqualifier overrides routing, not evaluation.

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

Open `.env` and fill in your keys. You need one LLM provider (pick one), Brave Search, and Notion:

```
# --- Required: pick ONE LLM provider ---
MODEL_PROVIDER=openai         # "openai", "anthropic", or "google"

# Then set the API key for your chosen provider (only one needed):
OPENAI_API_KEY=...            # required if MODEL_PROVIDER=openai
ANTHROPIC_API_KEY=...         # required if MODEL_PROVIDER=anthropic
GOOGLE_API_KEY=...            # required if MODEL_PROVIDER=google

# --- Required: search + output ---
BRAVE_SEARCH_API_KEY=...      # real-time company signal grounding
NOTION_TOKEN=ntn_...          # Notion integration token
NOTION_DATABASE_ID=...        # your Notion database ID (roles)

# --- Optional: run metrics tracking ---
RUN_METRICS_DATABASE_ID=...   # separate Notion DB for run metrics (optional)
```

**Choosing a model provider:**

Serai supports OpenAI, Anthropic (Claude), and Google Gemini interchangeably. You only need an API key for the one you choose. Set `MODEL_PROVIDER` in your `.env`:

| Provider | `MODEL_PROVIDER` | Default model | Approx. cost per role eval |
|---|---|---|---|
| OpenAI | `openai` | `gpt-4o-mini` | ~$0.02–0.04 |
| Anthropic | `anthropic` | `claude-sonnet-4-6` | ~$0.03–0.05 |
| Google Gemini | `google` | `gemini-2.5-flash` | ~$0.01–0.02 |

Override the model with `OPENAI_MODEL`, `ANTHROPIC_MODEL`, or `GOOGLE_MODEL` respectively (e.g., `GOOGLE_MODEL=gemini-2.5-flash-lite` for lower cost, `ANTHROPIC_MODEL=claude-opus-4-7` for maximum quality).

Where to get each key:

| Service | Where to get it |
|---|---|
| OpenAI | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Anthropic | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) |
| Google Gemini | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — generous free tier |
| Brave Search | [brave.com/search/api](https://brave.com/search/api/) — free tier covers typical usage |
| Notion | [notion.so/my-integrations](https://www.notion.so/my-integrations) — free plan works |

You'll set up the Notion database and get the database ID in step 5.

### 3. Configure your candidate profile

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and customize the candidate section:

- Your name and email
- Location and timezone
- Target roles and archetypes
- Which dimensions matter to you and how much (weights must sum to 1.0)
- Anchor companies at each score band for each dimension
- Disqualifiers (company categories to skip)
- Hard constraints (location, comp, level)

The company_preferences section defines your evaluation criteria. The discovery section controls discovery thresholds. The filters section handles title matching, location, and compensation screening. Each setting has comments explaining what it does.

### 4. Add your resume

```bash
cp examples/resume.example.md resume.md
```

Open `resume.md` and replace the example content with your own resume as plain text or markdown. Serai uses this for role-level scoring — it helps the LLM assess resume match, level fit, and scope alignment against job descriptions. Format doesn't need to be perfect. Strip out personal contact info (phone, address) if you prefer — Serai doesn't need it.

### 5. Create your Notion database

Create a new Notion database with these properties. Names and types must match exactly — Serai writes to these fields directly.

| Property | Type | Purpose |
|---|---|---|
| Final Recommendation | Select | Strong Apply / Apply / Skip |
| Screen Score | Number | Stage 1 screen score (1–5) |
| Deep Eval Score | Number | Stage 2 deep eval score (1–5) |
| Resume Match | Number | Resume match score (1–5) |
| Level Fit | Number | Level fit score (1–5) |
| Company Score | Number | Company dimension score (1–10) |
| Archetype | Select | Detected role archetype |
| Screen Route | Select | Stage 1 route |
| Legitimacy | Select | Posting legitimacy tier |
| Apply Urgency | Select | high / medium / low |
| Title | Text | Role title |
| Company | Text | Company name |
| URL | URL | Link to the job posting |
| Location | Text | Role location |
| Comp Min | Number | Minimum base compensation |
| Comp Max | Number | Maximum base compensation |
| Why Strong | Text | Top reasons this role scored well |
| Main Reservation | Text | Primary concern or risk |
| Source | Text | ATS source (e.g. greenhouse, ashby) |
| Source Job ID | Text | Job ID from the source ATS |
| First Seen | Date | When Serai first discovered this role |

Then connect your Notion integration to the database:

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) and create a new integration
2. Copy the integration's API key (starts with `ntn_`) into your `.env` as `NOTION_TOKEN`
3. Open your database in Notion, click the `...` menu, click "Connections", and add your integration
4. Copy the database ID from the URL (the 32-character string after your workspace name and before the `?`) into your `.env` as `NOTION_DATABASE_ID`

### 6. Add prose scoring guidance (optional)

```bash
cp examples/profile.example.md profile.md
```

This is optional enrichment. If provided, Serai uses it during role evaluation to get richer context about your archetype preferences, disqualifier explanations, and dimension anchors. If you skip this step, Serai works fine with just config.yaml and resume.md.

### 7. Run company discovery

```bash
python run_company_discovery.py
```

This scans ATS boards, discovers companies, scores them against your profile, evaluates roles at high-scoring companies using the two-stage funnel, and writes results to your Notion database. First run may take several minutes depending on how many companies are discovered.

### 8. Run the recurring role scan

```bash
python eval_llm_scoring.py
```

This checks for new roles at companies already in your active registry. Run it periodically to catch new postings.

### 9. Set up recurring runs (optional)

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

Serai answers a different question: "is this company the kind of place where I'd actually want to spend the next 3–5 years?" It evaluates companies on weighted dimensions, calibrated against anchor companies you define, grounded in real-time signals. No other job search tool does per-dimension company scoring with anchor calibration and signal routing.

The tools are complementary. Use Serai to find the right companies. Use whatever application tool you prefer to optimize your applications to them.

---

## Known limitations

- **Signal quality varies.** Brave Search grounding works well for well-known companies but can miss niche startups. Confidence ratings flag where evidence is thin
- **Token usage.** Stage 2 evaluation is more comprehensive (six dimensions, interviews, comp analysis), which costs more tokens than v1. The tradeoff is much better targeting — Stage 2 only runs on roles passing Stage 1, so you spend LLM tokens only on promising candidates
- **Profile investment.** The candidate profile requires thought. Writing good anchors is the difference between useful scores and noise. Templates in `examples/` help you get started
- **No UI.** Serai is a Python pipeline. You interact with results through Notion and configure it through text files. This is intentional for v1

## Roadmap

- **v1.3** Alerting (Notion + alternatives: Slack, email)
- **v1.4** `bin/generate_profile.py` — upload your resume + describe what you want, get a draft candidate profile to review and edit
- **v1.5** Historical accuracy tracking — did companies scored 8+ actually break out?
- **v2.0** Hosted version with resume-to-profile onboarding (if demand warrants)

---

## Credits

Built by [Alex Kassab](https://linkedin.com/in/alexandrakassab) during a job search. Role evaluation methodology adapted from [career-ops](https://github.com/santifer/career-ops) by [Santiago Fernández](https://santifer.io).

If Serai surfaces something interesting for you, I'd love to know.

## License

MIT
