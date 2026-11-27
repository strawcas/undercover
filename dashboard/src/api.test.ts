import { afterEach, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";
import { Refusal, claim, stubApi } from "./testing";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("reads the backend's JSON", async () => {
  stubApi({ "GET /api/claims/CLM-1002": claim });
  expect((await api.getClaim("CLM-1002")).summary).toBe(claim.summary);
});

it("raises the backend's reason for refusing", async () => {
  stubApi({ "GET /api/claims/CLM-1002": new Refusal(409, "Not now") });
  await expect(api.getClaim("CLM-1002")).rejects.toEqual(
    new ApiError(409, "Not now"),
  );
});

it("falls back to a general message when the reason isn't text", async () => {
  stubApi({
    "GET /api/configurations/current": new Refusal(422, [{ msg: "bad" }]),
  });
  await expect(api.getCurrentConfiguration()).rejects.toThrow(
    "The request failed",
  );
});
