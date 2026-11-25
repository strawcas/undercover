import {
  type SubmitEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { Handler } from "../api";
import { useAuth } from "../auth";
import { useCopilot } from "../useCopilot";
import { CopilotTurn } from "./CopilotTurn";
import "./copilot.css";

const OPEN_PREFERENCE = "undercover.copilot.open";
const OVERLAY_WIDTH = 900;
const SUGGESTIONS = [
  "What needs a handler today?",
  "What does the policy cover for delayed baggage?",
  "Are we meeting the success criteria we agreed?",
];

type Conversation = ReturnType<typeof useCopilot>;

interface ComposerProps {
  draft: string;
  setDraft: (value: string) => void;
  send: (draft: string) => Promise<void>;
  disabled: boolean;
  awaiting: boolean;
  error: string | null;
  name: string;
}

function Composer({
  draft,
  setDraft,
  send,
  disabled,
  awaiting,
  error,
  name,
}: Readonly<ComposerProps>) {
  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(draft);
  }
  return (
    <form className="copilot-composer" onSubmit={submit}>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="copilot-compose-row">
        <textarea
          aria-label="Ask the Copilot"
          maxLength={4000}
          rows={2}
          value={draft}
          placeholder={
            awaiting ? "Answer the confirmation above" : "Ask the Copilot"
          }
          disabled={disabled}
          onChange={(event) => {
            setDraft(event.target.value);
          }}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              void send(draft);
            }
          }}
        />
        <button
          className="primary"
          type="submit"
          disabled={disabled || !draft.trim()}
        >
          Send
        </button>
      </div>
      <p className="muted small">Signed in as {name}</p>
    </form>
  );
}

interface TranscriptProps {
  copilot: Conversation;
  name: string;
  send: (message: string) => Promise<void>;
  disabled: boolean;
  loading: boolean;
}

function Transcript({
  copilot,
  name,
  send,
  disabled,
  loading,
}: Readonly<TranscriptProps>) {
  const [clock, setClock] = useState(() => Date.now());
  const transcript = useRef<HTMLDivElement>(null);
  const last = copilot.detail?.turns.at(-1);
  const status = last?.status;
  const stepCount = last?.steps.length;
  useEffect(() => {
    if (status !== "working") return;
    const timer = setInterval(() => {
      setClock(Date.now());
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [status]);

  useEffect(() => {
    const element = transcript.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [copilot.selected, stepCount, status]);

  return (
    <div className="copilot-transcript" ref={transcript}>
      {loading && !copilot.error && (
        <p className="muted">Loading conversation…</p>
      )}
      {!loading && !last && (
        <div className="copilot-welcome">
          <h3>Hi, {name.split(" ")[0]}.</h3>
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              disabled={disabled}
              onClick={() => {
                void send(suggestion);
              }}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
      {copilot.detail?.turns.map((turn) => (
        <CopilotTurn
          key={turn.turn_id}
          turn={turn}
          now={clock}
          busy={copilot.busy}
          onConfirm={(id, confirmed) => {
            void copilot.confirm(id, confirmed);
          }}
        />
      ))}
    </div>
  );
}

export function CopilotSidebar({ handler }: Readonly<{ handler: Handler }>) {
  const { copilotToken } = useAuth();
  const [open, setOpen] = useState(
    () => localStorage.getItem(OPEN_PREFERENCE) !== "false",
  );
  const [draft, setDraft] = useState("");
  const toggle = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const copilot = useCopilot(open, copilotToken);
  const status = copilot.detail?.turns.at(-1)?.status;
  const loading =
    copilot.selected === undefined ||
    (copilot.selected !== null && !copilot.detail);
  const disabled =
    copilot.busy || status === "working" || status === "waiting" || loading;

  const changeOpen = useCallback((visible: boolean) => {
    setOpen(visible);
    localStorage.setItem(OPEN_PREFERENCE, String(visible));
    if (!visible)
      requestAnimationFrame(() => {
        toggle.current?.focus();
      });
  }, []);

  useEffect(() => {
    if (!open || window.innerWidth > OVERLAY_WIDTH) return;
    close.current?.focus();
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") changeOpen(false);
    };
    window.addEventListener("keydown", onEscape);
    return () => {
      window.removeEventListener("keydown", onEscape);
    };
  }, [open, changeOpen]);

  async function send(value: string) {
    const message = value.trim();
    if (message && !disabled && (await copilot.send(message))) setDraft("");
  }

  function select(id: string | null) {
    copilot.select(id);
    setDraft("");
  }

  if (!open)
    return (
      <button
        className="copilot-reopen"
        ref={toggle}
        type="button"
        onClick={() => {
          changeOpen(true);
        }}
      >
        <span aria-hidden="true" className="copilot-mark">
          ◇
        </span>{" "}
        Copilot
      </button>
    );

  return (
    <aside aria-label="UnderCover Copilot" className="copilot-sidebar">
      <header className="copilot-header">
        <div className="copilot-heading">
          <span aria-hidden="true" className="copilot-mark">
            ◇
          </span>
          <h2>UnderCover Copilot</h2>
          <button
            ref={close}
            className="copilot-close"
            type="button"
            aria-label="Collapse Copilot"
            onClick={() => {
              changeOpen(false);
            }}
          >
            ×
          </button>
        </div>
        <div className="copilot-history">
          <button
            type="button"
            disabled={copilot.busy}
            onClick={() => {
              select(null);
            }}
          >
            New conversation
          </button>
          <select
            aria-label="Earlier conversations"
            disabled={copilot.busy}
            value={copilot.selected ?? ""}
            onChange={(event) => {
              select(event.target.value || null);
            }}
          >
            <option value="">Earlier conversations</option>
            {copilot.history.map((item) => (
              <option key={item.conversation_id} value={item.conversation_id}>
                {item.title ?? "Untitled conversation"}
              </option>
            ))}
          </select>
        </div>
      </header>
      <Transcript
        copilot={copilot}
        name={handler.name}
        send={send}
        disabled={disabled}
        loading={loading}
      />
      <Composer
        draft={draft}
        setDraft={setDraft}
        send={send}
        error={copilot.error}
        awaiting={status === "waiting"}
        disabled={disabled}
        name={handler.name}
      />
    </aside>
  );
}
