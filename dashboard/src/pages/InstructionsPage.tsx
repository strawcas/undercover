import { api } from "../api";
import { Message } from "../components/Message";
import { useLoad } from "../useLoad";

export function InstructionsPage() {
  const { data, error } = useLoad(api.getInstructions);

  return (
    <>
      <h1>Agent instructions</h1>
      <p className="lead">
        What the agent reads before it starts: its instructions for working each
        claim, which end with the contents of the policy wording and the claims
        manual. This page is read only.
      </p>
      {data ? (
        <div className="column">
          <Instructions title="Working a claim" text={data.claim} />
        </div>
      ) : (
        <section className="panel">
          <Message error={error} />
        </section>
      )}
    </>
  );
}

function Instructions({
  title,
  text,
}: Readonly<{ title: string; text: string }>) {
  return (
    <section className="panel" aria-label={title}>
      <h2>{title}</h2>
      <pre className="instructions">{text}</pre>
    </section>
  );
}
