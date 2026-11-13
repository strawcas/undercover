import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
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

it("queues action refreshes behind a pending poll without overlapping or publishing stale data", async () => {
  vi.useFakeTimers();
  let finish: ((value: string) => void) | undefined;
  const pending = new Promise<string>((resolve) => {
    finish = resolve;
  });
  const load = vi
    .fn(() => Promise.resolve("Fresh after action"))
    .mockResolvedValueOnce("Initial")
    .mockReturnValueOnce(pending);
  function Probe() {
    const { data, reload } = useLoad(load, REFRESH_MS);
    return (
      <>
        <p>{data}</p>
        <button onClick={reload}>Action completed</button>
      </>
    );
  }
  render(<Probe />);
  await act(async () => {
    await Promise.resolve();
  });
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS));
  expect(load).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Action completed" }));
  fireEvent.click(screen.getByRole("button", { name: "Action completed" }));
  await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS * 2));
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Initial")).toBeDefined();
  await act(async () => {
    finish?.("Stale poll");
    await pending;
  });
  expect(load).toHaveBeenCalledTimes(3);
  expect(screen.queryByText("Stale poll")).toBeNull();
  expect(screen.getByText("Fresh after action")).toBeDefined();
});
