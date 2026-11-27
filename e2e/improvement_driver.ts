import { expect, type APIRequestContext } from "@playwright/test";
import { post, put } from "./api";

export const internalRound = (identity: string) =>
  `/api/improver/rounds/${encodeURIComponent(identity)}`;

export async function playEvaluations(
  request: APIRequestContext,
  identity: string,
  version: number,
  status: "baseline" | "evaluating",
) {
  let progress = 0;
  for (const name of ["evaluation", "held_out", "held_out"]) {
    await put(request, `${internalRound(identity)}/progress`, {
      status,
      progress: ++progress,
    });
    const response = await post(request, "/api/runs", {
      kind: "evaluation",
      set_name: name,
      tiny: false,
      concurrency: 25,
      configuration_version: version,
      round_id: identity,
    });
    const run = (await response.json()) as { run_id: string };
    await post(request, `/api/runs/${run.run_id}/finish`, {});
  }
  await post(
    request,
    `${internalRound(identity)}/${status === "baseline" ? "baseline" : "verdict"}`,
    {},
  );
}

export async function playProposal(
  request: APIRequestContext,
  identity: string,
  proposalId: string,
  kind: "configuration" | "recipe" = "configuration",
) {
  const current = await request.get("/api/configurations/current");
  await expect(current).toBeOK();
  const configuration = (await current.json()) as {
    instructions: string;
    model: string;
    reasoning_effort: string;
  };
  const argumentsValue =
    kind === "configuration"
      ? {
          model: configuration.model,
          reasoning_effort: configuration.reasoning_effort,
          instructions: `${configuration.instructions}\nUse the evidence to support the general policy rule.`,
          reason: "Improve evidence grounding",
        }
      : {
          recipe:
            "Review the development evidence before the next general change.",
          reason: "Learn from the tested change",
        };
  await post(request, `${internalRound(identity)}/proposals`, {
    proposal_id: proposalId,
    kind,
    arguments: argumentsValue,
  });
}
