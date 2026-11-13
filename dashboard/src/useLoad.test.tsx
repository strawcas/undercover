import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { REFRESH_MS, useLoad } from "./useLoad";
import { ApiError } from "./api";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("clears the previous resource while loading a new one and after its failure", async () => {
  const first = vi.fn(() => Promise.resolve("Claim A payout"));
  const missing = vi.fn(() =>
    Promise.reject(new ApiError(404, "Claim B not found")),
  );
  function Probe({ load }: Readonly<{ load: () => Promise<string> }>) {
    const { data, error } = useLoad(load);
    return <p>{data ?? error?.message ?? "Loading"}</p>;
  }
  const view = render(<Probe load={first} />);
  expect(await screen.findByText("Claim A payout")).toBeDefined();
  view.rerender(<Probe load={missing} />);
  expect(screen.queryByText("Claim A payout")).toBeNull();
  expect(await screen.findByText("Claim B not found")).toBeDefined();
  expect(screen.queryByText("Claim A payout")).toBeNull();
});

it("retains same-resource data on a temporary polling failure but removes it on a 404", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const load = vi
    .fn(() => Promise.resolve("Claim A payout"))
    .mockRejectedValueOnce(new Error("Unavailable"));
  function Probe() {
    const { data, error, reload } = useLoad(load);
    return (
      <>
        <p>{data}</p>
        <p>{error?.message}</p>
        <button onClick={reload}>Reload</button>
      </>
    );
  }
  render(<Probe />);
  await screen.findByText("Unavailable");
  fireEvent.click(screen.getByRole("button"));
  await screen.findByText("Claim A payout");
  load.mockRejectedValueOnce(new Error("Temporary failure"));
  fireEvent.click(screen.getByRole("button"));
  await screen.findByText("Temporary failure");
  expect(screen.getByText("Claim A payout")).toBeDefined();
  load.mockRejectedValueOnce(new ApiError(404, "Removed"));
  fireEvent.click(screen.getByRole("button"));
  await screen.findByText("Removed");
  expect(screen.queryByText("Claim A payout")).toBeNull();
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
