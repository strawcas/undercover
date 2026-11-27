import { afterEach, expect, it, vi } from "vitest";
import {
  addOnName,
  date,
  gradeScore,
  judgeCheck,
  label,
  modelCost,
  money,
  seconds,
  timestamp,
} from "./format";

afterEach(() => vi.unstubAllEnvs());

it("formats money in US dollars", () => {
  expect(money(1150)).toBe("$1,150.00");
  expect(money(0)).toBe("$0.00");
});

it("distinguishes ungraded explanations from an F score of zero", () => {
  expect(gradeScore(null)).toBe("Not graded");
  expect(gradeScore(0)).toBe("0.00 / 3");
  expect(gradeScore(1.6)).toBe("1.60 / 3");
});

it("shows saved judge probabilities as yes/no with a 0.5 threshold", () => {
  expect(judgeCheck(null)).toBe("Not graded");
  expect(judgeCheck(0)).toBe("No");
  expect(judgeCheck(0.499)).toBe("No");
  expect(judgeCheck(0.5)).toBe("Yes");
  expect(judgeCheck(1)).toBe("Yes");
});

it("formats dates without shifting the day", () => {
  expect(date("2026-06-24")).toBe("Jun 24, 2026");
});

it("formats starts in local time and completed seconds precisely", () => {
  vi.stubEnv("TZ", "Asia/Manila");
  expect(timestamp("2026-11-10T10:00:00Z")).toBe("Nov 10, 2026, 6:00:00 PM");
  vi.stubEnv("TZ", "UTC");
  expect(timestamp("2026-11-10T10:00:00Z")).toBe("Nov 10, 2026, 10:00:00 AM");
  expect(seconds(42.5)).toBe("42.5 s");
  expect(seconds(0)).toBe("0.0 s");
});

it("distinguishes unavailable model cost from a known free response", () => {
  expect(modelCost(null)).toBe("Unavailable");
  expect(modelCost(0)).toBe("$0.000000");
  expect(modelCost(0.000123)).toBe("$0.000123");
});

it("turns codes into labels", () => {
  expect(label("trip_delay")).toBe("Trip delay");
  expect(addOnName("electronics_plus")).toBe("Electronics Plus");
  expect(addOnName("new_add_on")).toBe("New add on");
});
