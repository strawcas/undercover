import { generatePath } from "react-router";

// The route of a claim's page, and the link to one claim's page.
export const CLAIM_PAGE = "/claims/:claimId";
export const claimPage = (claimId: string) =>
  generatePath(CLAIM_PAGE, { claimId });

export const SCOREBOARD_PAGE = "/scoreboard";
export const CONFIGURATION_PAGE = "/configuration";
export const SETTINGS_PAGE = "/settings";
export const EVALUATION_PAGE = `${SCOREBOARD_PAGE}/:runId`;
export const evaluationPage = (runId: string) =>
  generatePath(EVALUATION_PAGE, { runId });
