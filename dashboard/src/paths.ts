import { generatePath, matchPath } from "react-router";

// The route of a claim's page, and the link to one claim's page.
export const CLAIM_PAGE = "/claims/:claimId";
export const QUEUE_PAGE = "/";
export const claimPage = (claimId: string) =>
  generatePath(CLAIM_PAGE, { claimId });

export const SCOREBOARD_PAGE = "/scoreboard";
export const CONFIGURATION_PAGE = "/configuration";
export const SETTINGS_PAGE = "/settings";
export const IMPROVEMENT_PAGE = "/improvement";
export const EVALUATION_PAGE = `${SCOREBOARD_PAGE}/:runId`;
export const evaluationPage = (runId: string) =>
  generatePath(EVALUATION_PAGE, { runId });

export function claimNavigationSection(pathname: string) {
  const claimId = matchPath(CLAIM_PAGE, pathname)?.params.claimId;
  if (!claimId) return undefined;
  return claimId.startsWith("EVAL-") ? SCOREBOARD_PAGE : QUEUE_PAGE;
}
