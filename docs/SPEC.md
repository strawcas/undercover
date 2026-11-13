# The claims pilot

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy and our policy wording. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. For each claim it should:

- read the claim, the customer's policy and claim history, and the sections of the policy wording and the claims manual it needs
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

Our goal is to handle claims straight through, as accurately as our handlers do.

## Working the queue on its own

- A handler starts a dispatch from the dashboard, choosing from 1 to 25 claim agents at once, with 3 to start with. Only one dispatch or evaluation runs at a time. Code selects every waiting and in-progress claim, oldest submitted first, without a queue model.
- Each claim has its own claim agent and saved thread. A restart carries unfinished work on from its last saved step as the same attempt. Decisions and payments happen once even if a step runs again.
- An approval over the sign-off limit keeps its progress while it waits. When a handler signs off, the service resumes and pays it promptly, including during a dispatch. A signed-off claim remains in progress until paid. If the service is away, the next dispatch picks it up.
- Rate limits and timeouts wait and resume the same attempt, up to three worker tries. An exhausted claim stays in progress for the next dispatch while other claims finish. Other errors before a decision start a fresh attempt in the same dispatch; after two failed attempts the claim is left for a handler. A decision already recorded is retained if payment fails.
- The dispatch reports its members, cost, active claim time and elapsed time, then summarizes the backend records for handlers. Active time excludes waits for sign-off and process downtime.
- Evaluations work fresh copies of a labelled set, full or its five tiny claims, through the same claim agents. Copies stay out of the queue, cannot be signed off, and keep their decisions and steps after saved progress is cleared. Stopped evaluations remain stopped.

## What the team needs to see

- The queue and each claim in full, with its status, decision, reasons and work recorded step by step, including failed attempts and sections read.
- Controls to start a dispatch, its linked claims and progress, its own model cost and time, and a records-built summary naming held payouts and unresolved claims.
- A Scoreboard to start evaluations on either labelled set and compare newest-first results: decision match, payout accuracy within a dollar, explanation grade, cost of mistakes, straight-through rate, model cost per claim, active time per claim and run elapsed time. Each result opens handler and agent decisions/reasons/references side by side, shows the explanation's F/C/B/A letter, numerical score and same-grounds/section checks, and links to copied claims. Judge cost is separate from claim-agent cost. Undecided copies count as referrals and receive F; unavailable judge results are excluded from the average.
- A Configuration page showing the current claim-agent version, its instructions/model/reasoning effort, provenance, version history, changes and read-only document contents. Every dispatch and evaluation records the version it uses.
- A Settings page for the approval limit, improvement objectives, noise/tolerance, round spending limit and evaluation concurrency. Everyone signed in can view it; only senior handlers can change platform settings, with an audit trail. Allowed models and prices are file-owned and read only. Light/dark/system appearance is remembered by each browser.

## Rules

- A payout over the configured approval limit ($1,000 initially) needs a senior handler's sign-off. The agent can approve such a claim, but the payment waits until sign-off. The system blocks payment even if the agent tries to pay. Each decision retains its recorded approval limit; a new setting applies to subsequently recorded decisions.
- Decisions follow the policy wording and the claims manual. The wording comes first, and the manual says how to read it.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars.

## Handler sign-in

Handlers sign in with their existing Supabase email/password accounts. There is no sign-up. Every dashboard page needs a signed-in handler and shows their identity and a way to sign out. Starting a dispatch or evaluation requires a verified handler. Payout sign-off records the signed-in handler and requires senior authority: Dana Whitfield can sign off over the limit; Marcus Oyelaran and Priya Raman cannot. The backend enforces these permissions independently of the screen.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No real payments.
- No messages to customers.
