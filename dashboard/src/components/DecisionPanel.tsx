import type { Claim, Payment, PayoutCalculation } from "../api";
import { money, time, timestamp } from "../format";
import { DecisionChip } from "./Chips";
import { SignOffForm } from "./SignOffForm";

type SignOff = Pick<Payment, "signed_off_by" | "signed_off_at">;

function whoAndWhen({ signed_off_by, signed_off_at }: SignOff): string | null {
  return signed_off_by && signed_off_at
    ? `${signed_off_by} at ${time(signed_off_at)}`
    : null;
}

function progress(claim: Claim): string {
  if (claim.status === "in_progress")
    return "The agent is working on this claim.";
  if (claim.status === "left_for_handler")
    return `The agent couldn't finish this claim in ${String(claim.attempts)} attempts, so it's left for a handler.`;
  if (claim.attempts > 0) return "Back in the queue after a failed attempt.";
  return "The agent hasn't started on this claim.";
}

export function DecisionPanel({
  claim,
  onSignedOff,
}: Readonly<{ claim: Claim; onSignedOff: (paid: Claim) => void }>) {
  const { decision } = claim;

  return (
    <section className="panel" aria-labelledby="decision-title">
      <h2 id="decision-title">Decision</h2>
      {!decision && <p className="muted">{progress(claim)}</p>}
      {decision && (
        <>
          <div className="decision-line">
            <DecisionChip decision={decision.decision} />
            {decision.decision === "approve" && (
              <span className="payout mono">{money(decision.payout)}</span>
            )}
          </div>
          <p className="section-ref">{decision.section}</p>
          <p className="reason">{decision.reason}</p>
          <dl className="facts">
            <div>
              <dt>Decided by</dt>
              <dd>{decision.decided_by}</dd>
            </div>
            <div>
              <dt>Decided at</dt>
              <dd>
                <time dateTime={decision.decided_at}>
                  {timestamp(decision.decided_at)}
                </time>
              </dd>
            </div>
            <div>
              <dt>Manual version</dt>
              <dd className="mono">{decision.manual_version}</dd>
            </div>
          </dl>
          {decision.calculation && <Calculation value={decision.calculation} />}
        </>
      )}
      <PaymentStatus claim={claim} onSignedOff={onSignedOff} />
    </section>
  );
}

function Calculation({ value }: Readonly<{ value: PayoutCalculation }>) {
  return (
    <>
      <h3 id="payout-calculation">Payout calculation</h3>
      <div className="table-scroll">
        <table className="table" aria-labelledby="payout-calculation">
          <thead>
            <tr>
              <th scope="col">Line</th>
              <th scope="col" className="num">
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            {value.calculation.map((line, index) => (
              <tr key={`${String(index)}-${line.step}`}>
                <td>{line.step}</td>
                <td className="num mono">{money(line.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function PaymentStatus({
  claim,
  onSignedOff,
}: Readonly<{ claim: Claim; onSignedOff: (paid: Claim) => void }>) {
  const { decision, payment, status } = claim;
  if (payment) {
    const paidSignedOff = whoAndWhen(payment);
    return (
      <p className="payment">
        Paid <span className="mono">{money(payment.amount)}</span>
        {paidSignedOff && `, signed off by ${paidSignedOff}`}
      </p>
    );
  }
  if (decision?.decision !== "approve") return null;
  if (claim.sign_off_available || status === "waiting_for_sign_off")
    return (
      <HeldPayment
        claim={claim}
        payout={decision.payout}
        signedOff={whoAndWhen(decision)}
        onSignedOff={onSignedOff}
      />
    );
  return status === "in_progress" ? (
    <p className="payment">Waiting for the agent to pay it.</p>
  ) : null;
}

function HeldPayment({
  claim,
  payout,
  signedOff,
  onSignedOff,
}: Readonly<{
  claim: Claim;
  payout: number;
  signedOff: string | null;
  onSignedOff: (paid: Claim) => void;
}>) {
  const heldReason = claim.decision?.gateway_hold ? (
    <p>
      The Gateway refused the payment of{" "}
      <span className="mono">{money(payout)}</span>.
    </p>
  ) : (
    <p>
      The payment of <span className="mono">{money(payout)}</span> is over the
      approval limit
      {claim.evaluation_id ? "." : " and waits for a handler's sign-off."}
    </p>
  );
  return (
    <div className="sign-off">
      {signedOff ? (
        <p>Signed off by {signedOff}. The payout is ready to be recorded.</p>
      ) : (
        heldReason
      )}
      {claim.evaluation_id ? (
        <p>
          This evaluation is complete without sign-off. No payment is made for
          this held copy.
        </p>
      ) : (
        <SignOffForm
          claimId={claim.claim_id}
          onSignedOff={onSignedOff}
          previouslySignedOff={Boolean(signedOff)}
        />
      )}
    </div>
  );
}
