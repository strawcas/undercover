# The claims pilot

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy and our policy wording. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. For each claim it should:

- read the claim, the customer's policy and claim history, and the relevant policy wording and claims manual sections
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

Our goal is to handle claims straight through, as accurately as our handlers do.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off or decided.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step.
- The agent's instructions, read only.
- The current or latest run's claim-linked to-do list and factual summary.
- The full wording/manual sections returned to the agent, with their versions and assessment attempt.
- Handler review for a claim with two failed assessments, distinct from an insurance referral.

## The harness

An explicit terminal command starts a queue through the agent's service. Starting or restarting the backend, service or dashboard admits no fresh claims. `uv run -m agent` works the eligible waiting snapshot; `uv run -m agent --cap 5` works up to five distinct claims. One queue agent chooses the order, plans its work and delegates claims one at a time. Named-claim mode remains available. Later arrivals and cap-excluded claims wait for another command; dashboard polling only reads progress.

Each claim assessment saves its steps. A stopped assessment continues with the same attempt and thread; the next command first finishes the stopped queue's saved scope and cap. It does not take a new snapshot afterward. A genuine assessment error starts a fresh attempt, as described below.

The backend applies the payout rules through a calculator available to the claim agent. Every approval reaches the graph's payment step. When the backend requires sign-off, the graph saves its work and waits. Signing off saves the handler and time, puts the claim In progress, and notifies the service. The same assessment resumes to record the simulated payment and its sign-off attribution. If notification cannot reach the service, the next explicit command finds the saved approval. The queue can finish while a held claim waits; its completion-time summary stays a snapshot after later sign-off/payment.

An undecided failed assessment returns to Waiting for one automatic retry, including during the same run. Two failures leave the claim for a handler without inventing a decision or payout. Failure counts survive restarts; interruption and human waiting consume no failure allowance. Matching decision/payment repeats return the stored claim without another financial event; different decisions or amounts are refused. The backend fences stale executions and reconciles ambiguous write responses. The queue checks for missed work before finishing.

The claims manual supplements the policy wording with handling and interpretation rules; the wording remains the contract. Instructions and evidence come from the permitted business sources, never the held-out handler decisions. Each claim helper selects and reads sections through the backend API. Both roles' effective instructions are visible read only.

## Rules

- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No sign-in. Everyone who uses the screens sees everything.
- No real payments.
- No messages to customers.
