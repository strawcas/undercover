# The claims pilot

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy and our policy wording. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. For each claim it should:

- read the claim, the customer's policy and claim history, and the sections of the policy wording and the claims manual it needs
- use the context layer's definitions, plans, claim rules, field descriptions and outside facts as needed
- approve, decline or refer the claim
- work out the payout when it approves
- record the decision, payout, calculation for an approval, relevant sections, a reason in two or three sentences, who made the decision, the time and the claims manual's version

Our goal is to handle claims straight through, as accurately as our handlers do.

## Working the queue on its own

- A handler starts a dispatch from the dashboard, choosing from 1 to 25 claim agents at once, with 3 to start with. Only one dispatch or evaluation runs at a time. Code selects every waiting and in-progress claim, oldest submitted first, without a queue model.
- Each claim has its own claim agent and saved thread. A restart carries unfinished work on from its last saved step as the same attempt. Decisions and payments happen once even if a step runs again.
- Before adjudication, a separate model screens the customer's submission for directions to the claims handler or purported prior approval. A flag results in a zero-payout referral with the screening reason and the reference `Security check`. Cleared claims continue through adjudication. The timeline starts with the screening result, and screening calls count toward claim cost. Saved work carries the screen forward. A fresh attempt checks the submission again.
- A held original approval is paid when a senior handler signs off. The backend records the signer, timestamp, payment and decided state together, including when the agent service is offline.
- Rate limits and timeouts wait and resume the same attempt, up to three worker tries. An exhausted claim stays in progress for the next dispatch while other claims finish. Other errors before a decision start a fresh attempt in the same dispatch; after two failed attempts the claim is left for a handler. A decision already recorded is retained if payment fails.
- The dispatch reports its members, cost, active claim time and elapsed time, then summarizes the backend records for handlers. Active time excludes waits for sign-off and process downtime.
- Evaluations work fresh copies of a labelled set, full or its five tiny claims, through the same claim agents. Copies stay out of the queue, cannot be signed off, and keep their decisions and steps after saved progress is cleared. Stopped evaluations remain stopped.

## What the team needs to see

- The queue and each claim in full, with its complete decision, payout calculation and a timeline of tool calls, sourced lookups, memory searches and saves, refusals and failed attempts. Context used groups unique lookups by source file.
- Controls to start a dispatch, its linked claims and progress, its own model cost and time, and a records-built summary naming held payouts and unresolved claims.
- A Scoreboard to start evaluations on either labelled set and compare newest-first results: decision match, payout accuracy within a dollar, explanation grade, cost of mistakes, straight-through rate, model cost per claim, active time per claim and run elapsed time. Each result opens handler and agent decisions/reasons/references side by side, shows the explanation's F/C/B/A letter, numerical score and same-grounds/section checks, and links to copied claims. Judge cost is separate from claim-agent cost. Undecided copies count as referrals and receive F; unavailable judge results are excluded from the average.
- A Configuration page showing the current claim-agent version, its instructions/model/reasoning effort, provenance, version history, changes and read-only document contents. Every dispatch and evaluation records the version it uses.
- A Settings page for the approval limit, improvement objectives, noise/tolerance, round spending limit and evaluation concurrency. Everyone signed in can view it; only senior handlers can change platform settings, with an audit trail. Allowed models and prices are file-owned and read only. Light/dark/system appearance is remembered by each browser.
- An Improvement page for senior handlers to start/stop rounds, approve or reject proposed configuration and recipe changes, and review durable progress, diffs, decisions, spending and before/after held-out scores. All handlers can view the history and accuracy/grade charts. An approved configuration is tested before the backend keeps or rolls it back; an approved recipe applies to future rounds.

## Improving the agent

Configurations and recipes are immutable backend-owned versions. New work pins the current configuration; a round captures its settings and recipe. The improver sees development examples and held-out aggregates, while claim execution sees no labels or scores. The backend judges explanations and owns the keep/rollback rule. Only completed whole-set round evaluations contribute to version scores.

Initial settings are accuracy/grade floors of 0.60/1.00, noise of 0.10/0.15/0.15/0.15 and tolerance of 0.10/0.15/1.00/0.25 for accuracy/grade/cost/time. Accuracy and grade use absolute differences; cost and time use relative differences. Each round starts with a $0.50 limit covering its improver, claim evaluations and judge, with 25 concurrent claim agents. Missing evidence or an unaffordable candidate rolls back. The default claim model is Luna at low effort and the improver uses Luna at medium effort. A configured free-model override disables grading and records default effort.

Every dashboard page has a clear header and consistent controls in light and dark mode. Statuses include words, tables show units, and wide comparisons scroll locally. Settings shows accuracy floors as percentages, accuracy noise/tolerance as percentage points, cost/time thresholds as relative percentages, and grades as score points. Save confirmations and settings history use the same units. Held evaluation copies are complete without payout sign-off and stay outside the queue.

## Rules

- A payout over the configured approval limit ($1,000 initially) needs a senior handler's sign-off. The agent can approve a claim above the limit. Agent payment checks the current limit before looking up the claim, and a decision retains the limit recorded with it. An existing hold carries forward after a limit increase. A senior handler can settle an older unpaid approval blocked by a limit reduction. Held evaluation copies stay complete with payment held.
- A senior handler can send an unpaid held approval back with a note of up to 1,000 characters. The claim retains the earlier decision, note, handler and review time. The agent works from that review and the policy wording to record a replacement decision. The next dispatch picks up a returned claim if the agent missed the request or stopped answering. A replacement approval follows the normal payout and sign-off rules.
- Decisions follow the policy wording and the claims manual. The wording comes first, and the manual says how to read it.
- An approval uses the app's latest calculation for that attempt. The decision records the configuration version used by the UnderCover Agent, or the security check as maker of its own referrals.
- Configured Gateway Policy covers the model's forwarded tools and graph payment. If the Gateway holds an original payout, a senior handler can settle it through the app.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars.

## Handler sign-in

Handlers sign in with their existing Supabase email/password accounts. There is no sign-up. Every dashboard page needs a signed-in handler and shows their identity and a way to sign out. Starting a dispatch or evaluation requires a verified handler. Payout sign-off records the signed-in handler and requires senior authority: Dana Whitfield can sign off over the limit; Marcus Oyelaran and Priya Raman cannot. The backend enforces these permissions independently of the screen.

Private calls between the backend and the agent service use the shared agent API key. Shared claim and configuration reads also accept a verified handler. The health endpoint is public.

## Our records

`data/` contains the queue, policies, customers, handlers and seed settings. The held-out claims in `data/evals/` support evaluation separately from the queue. [Seed data](../data/README.md)

Data governance owns the read-only files under `context/`. Seeding takes the plans and the two versioned documents from those files. The agent reads definitions, plans, rules, reference data and document sections through the context service.

When memory is configured, the agent remembers a cleared original claim by saving its summary, benefit, incident date and place, items and recorded decision as two events scoped to its customer. The story and supporting documents stay on the claim. The official history governs the repeat-claims rule. Customer memory is available to original-claim graphs.

## Not for now

- No real payments.
- No messages to customers.
