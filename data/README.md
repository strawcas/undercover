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
- `claims.json` provides the twenty-five original queue claims, each with the customer's story, items and supporting documents as text. The queue seed also includes CLM-1901.

## The documents

- `policy_wording.md`: the contract with the customer. Short enough to fit in a prompt.
- `claims_manual.md`: the internal handling manual. Longer, and it holds rules the wording does not, such as the third claim in 12 months, losses to wild animals and meals without receipts.

## The evaluation set

- `evals/decisions.json` gives the handlers' decisions on the twenty-five claims from `claims.json`, including the outcome, payout, calculation, reason and what each claim tests. The labels support evaluation and authorized review.
- `evals/held_out_claims.json` and `evals/held_out_decisions.json`: twenty-five more claims with their decisions, kept out of the queue for checking that an improvement generalizes. They have the same blend as the queue. Keep them out of sight while you write a prompt or a rule, and score them afterwards.

The labelled originals are the development set and the held-out claims are the test set. Both sets are used for evaluation.

Every decision also carries a few tags. `difficulty` is `routine`, `hard` (the wording settles it and it stretches a small model) or `manual` (it needs something the prompt does not hold, listed in `needs`: the claims manual, the customer's history or both). `failure_class` groups the hard claims that one rule should fix. `tone` is `plain`, `amusing` or `nod`. `tiny` marks a subset of five claims in each set, one of each kind, which fits inside a free model's daily allowance.

The labs define `get_sample_data(tiny=False, held_out=False)`, which returns the claims of either set with their decisions.

A decision is scored on two things: the decision itself and the payout. A valid claim that pays more than $1,000 is still an approve. The payment waits for a handler's sign-off, and `over_approval_limit` marks those claims.

## The security claim

- Seeding adds CLM-1901 and its linked policy and customer from `security/`. The queue has 26 originals. Its handler decision remains a lab fixture. The existing labels select the same 25 full or five tiny claims in each evaluation set.

## Improvement evidence

`improvement_settings.json` supplies the initial objectives, noise/tolerance, $0.50 round budget and concurrency of 25. `allowed_models.json` supplies allowed efforts and input/output prices per million tokens; the dashboard displays this catalogue read only. `company.json` supplies the initial $1,000 approval limit. After seeding, database records own platform settings and attributed changes. Decisions retain the approval limit that applied when recorded. The initial recipe is `backend/instructions/recipe.md`; the initial configuration uses the existing claim instructions and default Luna low. The improver uses Luna medium. An explicit `MODEL_NAME` instead seeds the sole free-model option at default effort, zero catalogue prices and no judge grading.

Seeding resets configuration and recipe versions to 1 and clears round, proposal, evaluation, usage and settings-audit records. Each new round gets fresh UUID identities, including its checkpoint thread. It captures platform settings and the model catalogue; later setting edits apply to future rounds. Backend reports give the improver development-set summaries, handler reasons, weak graded explanations and both sets' aggregate scores. Held-out claim contents and labels remain backend-only, available to the judge and signed-in dashboard views. Claim execution receives neither set's labels or scores. Supabase Auth accounts/passwords are never created or changed by seeding.
