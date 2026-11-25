import { useEffect, useRef, useState } from "react";
import { copilotApi as api } from "./copilotApi";
import type { CopilotConversation, CopilotDetail } from "./copilotTypes";

const POLL_MS = 1000;

function reason(error: unknown) {
  return error instanceof Error ? error.message : "The Copilot is unavailable.";
}

export function useCopilot(open: boolean, getToken: () => Promise<string>) {
  const [history, setHistory] = useState<CopilotConversation[]>([]);
  const [selected, setSelected] = useState<string | null | undefined>(
    undefined,
  );
  const [detail, setDetail] = useState<CopilotDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, reload] = useState(0);
  const pending = useRef(false);
  const read = useRef<AbortController | null>(null);
  const mutating = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      mutating.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (!open) return;
    const request = new AbortController();
    void api.listCopilotConversations(request.signal).then(
      (items) => {
        if (request.signal.aborted) return;
        setHistory(items);
        setSelected((current) =>
          current === undefined ? (items[0]?.conversation_id ?? null) : current,
        );
      },
      (failure: unknown) => {
        if (request.signal.aborted) return;
        setError(reason(failure));
        setSelected((current) => current ?? null);
      },
    );
    return () => {
      request.abort();
    };
  }, [open, revision]);

  useEffect(() => {
    if (!open || !selected) return;
    const id = selected;
    const request = new AbortController();
    read.current = request;
    let next: ReturnType<typeof setTimeout> | undefined;
    let working = pending.current;

    async function poll() {
      try {
        const conversation = await api.getCopilotConversation(
          id,
          request.signal,
        );
        if (request.signal.aborted) return;
        setDetail(conversation);
        if (conversation.turns.at(-1)?.status === "working") setError(null);
        if (pending.current) {
          pending.current = false;
          mutating.current = null;
          setBusy(false);
        }
        working = conversation.turns.at(-1)?.status === "working";
      } catch (failure) {
        if (!request.signal.aborted) setError(reason(failure));
      }
      if (working && !request.signal.aborted) {
        next = setTimeout(() => void poll(), POLL_MS);
      }
    }

    void poll();
    return () => {
      request.abort();
      clearTimeout(next);
    };
  }, [open, selected, revision]);

  function select(id: string | null) {
    setError(null);
    setSelected(id);
  }

  async function mutate(
    submit: (token: string, signal: AbortSignal) => Promise<void>,
  ) {
    if (mutating.current) return false;
    const update = new AbortController();
    mutating.current = update;
    setBusy(true);
    setError(null);
    let submitting = false;
    try {
      const token = await getToken();
      update.signal.throwIfAborted();
      submitting = true;
      await submit(token, update.signal);
      update.signal.throwIfAborted();
      read.current?.abort();
      pending.current = true;
      reload((value) => value + 1);
      return true;
    } catch (failure) {
      if (!update.signal.aborted) {
        mutating.current = null;
        setError(reason(failure));
        setBusy(false);
        if (submitting) reload((value) => value + 1);
      }
      return false;
    }
  }

  function send(message: string) {
    return mutate(async (token, signal) => {
      const id =
        selected ??
        (await api.createCopilotConversation(token)).conversation_id;
      signal.throwIfAborted();
      setSelected(id);
      await api.sendCopilotMessage(id, message, token);
      signal.throwIfAborted();
    });
  }

  function confirm(turnId: string, confirmed: boolean) {
    return mutate(async (token) => {
      if (selected)
        await api.answerCopilotConfirmation(selected, turnId, confirmed, token);
    });
  }

  return {
    history,
    selected,
    detail: detail?.conversation_id === selected ? detail : null,
    error,
    busy,
    select,
    send,
    confirm,
  };
}
