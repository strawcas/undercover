import { Link } from "react-router";
import { api, statuses } from "../api";
import { DecisionChip, StatusChip } from "../components/Chips";
import { Message } from "../components/Message";
import { RunPanel } from "../components/RunPanel";
import { PageHeader } from "../components/PageHeader";
import { date, label, money, statusLabels } from "../format";
import { claimPage } from "../paths";
import { REFRESH_MS, useLoad } from "../useLoad";

export function QueuePage() {
  const { data: claims, error, reload } = useLoad(api.listClaims, REFRESH_MS);
  const header = (
    <PageHeader title="Claims queue">
      Start a dispatch, follow claim progress and review payouts waiting for
      sign-off.
    </PageHeader>
  );
  if (!claims)
    return (
      <>
        {header}
        <Message error={error} retry={reload} />
      </>
    );

  return (
    <>
      {header}
      {error && <Message error={error} retry={reload} />}
      <section className="tiles" aria-label="Claims by state">
        {statuses.map((status) => {
          const count = claims.filter((c) => c.status === status).length;
          return (
            <div
              className="panel tile"
              key={status}
              aria-label={`${statusLabels[status]}: ${String(count)}`}
            >
              <StatusChip status={status} />
              <span className="tile-count mono">{count}</span>
            </div>
          );
        })}
      </section>
      <RunPanel />
      {claims.length === 0 && (
        <p className="message">
          There are no claims in the queue. New claims will appear here when
          available.
        </p>
      )}
      <section className="panel table-scroll" aria-label="Claims queue table">
        <table className="table">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Customer</th>
              <th>Type</th>
              <th>Summary</th>
              <th className="num">Claimed (USD)</th>
              <th>Submitted</th>
              <th>State</th>
              <th>Decision</th>
              <th className="num">Payout (USD)</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => (
              <tr key={claim.claim_id}>
                <td className="nowrap">
                  <Link className="mono" to={claimPage(claim.claim_id)}>
                    {claim.claim_id}
                  </Link>
                </td>
                <td>{claim.customer_name}</td>
                <td>{label(claim.claim_type)}</td>
                <td>{claim.summary}</td>
                <td className="num mono">{money(claim.amount_claimed)}</td>
                <td className="nowrap">{date(claim.submitted_on)}</td>
                <td>
                  <StatusChip status={claim.status} />
                </td>
                <td>
                  {claim.decision && <DecisionChip decision={claim.decision} />}
                </td>
                <td className="num mono">
                  {claim.payout !== null && money(claim.payout)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
