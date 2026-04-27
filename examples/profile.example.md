# Optional Scoring Guidance Profile

**This file is optional.** It provides prose context during role evaluation — dimension anchors with examples, archetype definitions, and disqualifier explanations. If you omit this file, Serai works fine with just `config.yaml` and `resume.md`.

Use this file to document your thinking about what makes a role attractive or unattractive. Serai references this during evaluation for richer context.

---

## About This Candidate

Jordan Chen, Senior Infrastructure Engineer with 10+ years building distributed systems at scale. Strongest work has been on platform infrastructure (internal tooling, CI/CD, Kubernetes), system reliability under load, and leading infrastructure teams. Seeking Staff-level roles where infrastructure is a strategic advantage, not a cost center.

What makes Jordan energized: shipping systems that let other engineers move faster. What drains him: organizations where infrastructure is treated as a commodity.

---

## Archetype Deep-Dive

### Strong Fit: Platform Engineer

These companies treat internal developer velocity as a competitive edge. Engineering leadership actively invests in platform infrastructure. Examples: Stripe (payments infrastructure), Figma (collaborative systems), Ramp (corporate cards + infrastructure). What to look for: hiring multiple platform engineers, published engineering blogs on tooling, internal platform dashboards or observability stacks that the company highlights.

### Strong Fit: DevOps / SRE

Companies with real-time constraints, large-scale operations, or high-reliability requirements. Examples: Datadog (monitoring at scale), Heroku (platform reliability), Discord (millions of concurrent users). Look for: SRE culture signals, incident postmortems, talks about chaos engineering or gamedays.

### Moderate Fit: Backend Systems

Large-scale backend work, but only if it involves infrastructure challenges. A company doing CRUD APIs at modest scale is not attractive. High fit if: microservices at 100K+ RPS, custom database work, distributed consensus.

### Weak Fit: Data Infrastructure

More interesting than generic data engineering, but not primary passion. OK if the company is hypergrowth and data infrastructure is strategic (Databricks, Scale AI). Skip if data platform is not core business.

### Weak Fit: Cloud Architecture

Only if role involves hands-on engineering, not just consulting. Many "architect" roles are political. Seek companies where architecture work is adjacent to systems ownership.

---

## Dimension Anchors with Context

### Engineering Depth (weight: 0.30 — highest priority)

This is the single biggest driver of job interest. Jordan wants to work on technically hard problems.

**9-10 (Strong YES):** Stripe (payments processor solving real-time settlement), Cloudflare (edge network optimization), Anthropic (training infrastructure for frontier models), Databricks (distributed query optimization).

**7-8 (YES):** Ramp (corporate card infra + reporting at scale), Linear (real-time collab), Figma (WebGL rendering pipeline).

**5-6 (MAYBE):** A competent SaaS company using modern stack (Kubernetes, event streaming) but not pushing boundaries.

**3-4 (NO):** CRUD APIs, typical fintech consumer apps, standard web stack.

**Red flag:** Companies using only managed services, no custom infrastructure, engineers don't understand their own systems.

### Moat Durability (weight: 0.20)

A company with structural advantages pays better, lasts longer, and gives you more leverage to solve hard problems.

**9-10:** Stripe (payments network effects), Cloudflare (protocol-level lock-in), Databricks (data + ecosystem).

**7-8:** Datadog (switching costs), Figma (users and data locked in).

**Red flag:** Commodity product, easy to replicate, customer acquisition only differentiator.

### Growth Trajectory (weight: 0.20)

Growth = new technical challenges. Stagnation = boring maintenance.

**9-10:** Anthropic, OpenAI, Harvey — scaling creates novel infra problems daily.

**7-8:** Ramp, Databricks — strong growth, clear infrastructure needs.

**Red flag:** "Steady state" or declining growth, risk of internal reorganizations, infrastructure budgets being cut.

### AI Leverage (weight: 0.15)

Relevant but not dominant unless AI IS the infrastructure challenge. A fintech company using ChatGPT for customer support doesn't score here. Anthropic training LLMs scores highly.

**9-10:** Anthropic, OpenAI, Databricks, Scale AI — AI infra is core.

**7-8:** Harvey, Ramp (using AI in infra decisions).

**Red flag:** "We use AI for ___" without infrastructure implications.

### Leadership Quality (weight: 0.10)

Technical leadership credibility determines whether infra investment is real or cosmetic.

**9-10:** CTO is a respected engineer (Stripe: Patrick Collison founded it, Eric Jean-Baptiste VPE). VP Engineering comes from engineering, not sales.

**7-8:** Technical founders, or engineering leaders promoted from within.

**Red flag:** CEO is from finance/sales, CTO is new, VP Engineering is third in 18 months.

### Market Category (weight: 0.05)

Category growth sustains long-term infra investment. Doesn't matter if you solve hard problems in a shrinking market.

**9-10:** Cloud, AI, fintech — expanding TAM, infra increasingly important.

**7-8:** Developer tools, security — steadily growing.

**Red flag:** Declining category, risk of consolidation.

---

## Disqualifier Definitions

**Consultancy / Agency**

Company's primary business is building software for *other* companies, not owning and operating its own product. Example: Accenture, McKinsey Digital, Deloitte Consulting. Even if technical work is interesting, your impact is limited — you're just staffed onto client projects.

**Legacy Enterprise**

Established company (10+ years) where infrastructure decisions are driven by procurement and vendor relationships, not engineering judgment. Platform hasn't been meaningfully rearchitected in 5+ years. Example: IBM, Oracle, legacy banking software. Red flags: COBOL still in production, PeopleSoft running the business, VendorLock, procurement committee approves tech decisions.

**Consumer Social / Gaming**

Company's primary business is consumer-facing social or gaming. Infrastructure may be interesting (Discord's scale is real) but product domain mismatch. Skip unless clear internal role in systems teams (and even then, user-facing constraints limit architectural freedom).

---

## Role-Level Criteria

When evaluating a specific role posting, screen for:

**Must-haves:**
- Remote or Seattle metro location
- Base $220K+, total comp $350K+ (senior/staff level)
- Infrastructure, platform, or systems focus
- Team has ownership of their systems, not just ops tickets

**Strong signals:**
- Hiring multiple engineers into the same team (indicates growth, not just coverage)
- Published engineering blog (signals they think deeply about their work)
- Kubernetes, EKS, or custom infrastructure mentioned
- "On-call" culture explicitly described (shows reliability matters)
- "RFC process" or "architecture review" mentioned (signals intentionality)

**Red flags:**
- "No remote" or "must be in X city" (unless it's Seattle)
- Uses only managed services (no Kubernetes, all Lambda/Dynamodb)
- "Owning systems" described as "ticket triage" or "bug fixes"
- Title says "Senior" but reports to non-technical manager
- Recently had VP Engineering turnover (instability signal)
- Job posting recycled from last year (role not filled, might be broken)

---

## Interview Prep — What to Ask

When you get an interview, clarify:

1. "What's the biggest infrastructure challenge your team solved in the last 12 months?" (Look for thoughtfulness, not buzzwords.)
2. "Walk me through your incident response process — what's a recent postmortem?" (Shows maturity and ownership.)
3. "How does your team balance technical debt with feature velocity?" (Reveals whether infra is valued or sacrificed.)
4. "What's your current on-call rotation?" (Some overload signals dysfunction.)
5. "Show me your architecture diagram — what'd you change if you started over today?" (Candor and realism are good.)

---

## Final Notes

This guidance is about *preferences*, not absolutes. A company that's 6/10 on paper but has the exact technical problem you've dreamed about solving is worth exploring. Conversely, a company that's 8/10 on all dimensions but has toxic leadership is skip.

Trust your gut after Stage 1 screen passes.
