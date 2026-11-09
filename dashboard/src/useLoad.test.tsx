import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { REFRESH_MS, useLoad } from "./useLoad";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("waits for a slow request before polling again", async () => {
  vi.useFakeTimers();
  let finish: ((value: string) => void) | undefined;
  const pending = new Promise<string>((resolve) => {
    finish = resolve;
  });
  const load = vi
    .fn(() => Promise.resolve("Updated"))
    .mockReturnValueOnce(pending);
  function Probe() {
    const { data } = useLoad(load, REFRESH_MS);
    return <p>{data ?? "Loading"}</p>;
  }
  render(<Probe />);
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS * 3));
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => {
    finish?.("Ready");
    await pending;
  });
  expect(screen.getByText("Ready")).toBeDefined();
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS));
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Updated")).toBeDefined();
});
