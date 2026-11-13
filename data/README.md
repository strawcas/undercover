# UnderCover seed data

The data behind UnderCover Travel Insurance, a fictional insurer. Everything here is invented, including the people, the airlines and the hotels. Amounts are in US dollars.

## The insurer's systems

- `company.json`: company settings, including the initial $1,000 payout approval limit.
- `improvement_settings.json`: initial business objectives, noise/tolerance, round budget and evaluation concurrency. Together with the approval limit these seed database-owned settings; senior handlers' later edits are audited there.
- `allowed_models.json`: file-owned model identifiers, allowed reasoning efforts and numeric input/output prices in US dollars per million tokens. The dashboard lists these without editing them. An explicit `MODEL_NAME` seeds the free-model override as the sole zero-price entry at `default` effort.
- `plans.json`: the three plans (Economy, Premium, First) with their limits, waiting periods and deductibles, and the three add-ons.
- `policies.json`: one policy per trip, with the plan, the add-ons, the purchase date, the trip and the travelers.
- `customers.json`: contact details and claim history, with the date and a one line summary of each earlier claim. This is personal data.
- `handlers.json`: the claims handlers, their sign-in emails and permission to sign off payouts over the limit. Only Dana Whitfield has senior authority. Passwords and Supabase Auth users are managed outside the seed data.
- `claims.json`: the claims queue, twenty-five claims. Each has the customer's story, the items claimed and the supporting documents as text.

## The documents

- `policy_wording.md`: the contract with the customer. Short enough to fit in a prompt.
- `claims_manual.md`: the internal handling manual. Longer, and it holds rules the wording does not, such as the third claim in 12 months, losses to wild animals and meals without receipts.

## The evaluation set

- `evals/decisions.json`: the handlers' decisions on the twenty-five claims in the queue: approve, decline or refer, the payout, the calculation, the reason and what each claim tests. An agent deciding claims must never be shown this file.
- `evals/held_out_claims.json` and `evals/held_out_decisions.json`: twenty-five more claims with their decisions, kept out of the queue for checking that an improvement generalizes. They have the same blend as the queue. Keep them out of sight while you write a prompt or a rule, and score them afterwards.

The queue is the development set and the held-out claims are the test set. Nothing is trained on either.

Every decision also carries a few tags. `difficulty` is `routine`, `hard` (the wording settles it and it stretches a small model) or `manual` (it needs something the prompt does not hold, listed in `needs`: the claims manual, the customer's history or both). `failure_class` groups the hard claims that one rule should fix. `tone` is `plain`, `amusing` or `nod`. `tiny` marks a subset of five claims in each set, one of each kind, which fits inside a free model's daily allowance.

The labs define `get_sample_data(tiny=False, held_out=False)`, which returns the claims of either set with their decisions.

A decision is scored on two things: the decision itself and the payout. A valid claim that pays more than $1,000 is still an approve. The payment waits for a handler's sign-off, and `over_approval_limit` marks those claims.

## The security claim

- `security/`: CLM-1901, a stolen watch whose story carries an instruction to the AI to approve it in full, with its policy, its customer and the handler's decision (decline). It is a prompt injection kept for security testing, in a folder of its own, apart from the queue and the evaluation sets.

## Improvement evidence

Seeding resets configuration and recipe versions to 1 and clears round, proposal, evaluation, usage and settings-audit records. Each new round gets fresh UUID identities, including its checkpoint thread. It captures platform settings and the model catalogue; later setting edits apply to future rounds. Backend reports give the improver development-set summaries, handler reasons, weak graded explanations and both sets' aggregate scores. Held-out claim contents and labels remain backend-only, available to the judge and signed-in dashboard views. Claim execution receives neither set's labels or scores. Supabase Auth accounts/passwords are never created or changed by seeding.
