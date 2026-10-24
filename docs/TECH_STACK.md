# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount.
- `agent/`: LangChain's `create_agent`, with one entry point that takes a claim id and a thread id. It reaches claims and its instructions only through the backend's API. `MODEL_PROVIDER` selects `openrouter` (the default, using `ChatOpenAI`) or `ollama` (using `langchain-ollama`'s `ChatOllama`). OpenRouter uses `openai/gpt-6-luna` with low reasoning effort by default, or `MODEL_NAME` if set, such as `openrouter/free`. Ollama requires `MODEL_NAME` to name a downloaded model that supports tool calling. `OLLAMA_BASE_URL` defaults to `http://localhost:11434`.
- `dashboard/`: React, Vite, Tailwind CSS and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. Configure the provider and model in the root `.env`. OpenRouter requires `OPENROUTER_API_KEY`; local Ollama requires no API key.
