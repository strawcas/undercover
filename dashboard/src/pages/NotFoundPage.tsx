import { Link } from "react-router";
import { PageHeader } from "../components/PageHeader";

export function NotFoundPage() {
  return (
    <PageHeader
      title="Page not found"
      actions={
        <Link className="button-link secondary" to="/">
          Return to queue
        </Link>
      }
    >
      This address does not match a dashboard page. Return to the queue to
      continue working claims.
    </PageHeader>
  );
}
