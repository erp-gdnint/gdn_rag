# GDN RAG — Configuration

All settings are JSON. There are no DocTypes in v1. All code reads settings through
`gdn_rag/rag/config.py` (`get_config()`), so a DocType-based store can replace the JSON
store later without changing other code.

## Where settings come from

A later layer wins over an earlier one.

| Layer | File | Purpose |
|---|---|---|
| 1 | `gdn_rag/rag_config/defaults.json` | All settings with default values. Committed. |
| 2 | `sites/<site>/site_config.json` → key `"gdn_rag"` | Partial override per site (any key from layer 1). |
| 3 | `sites/<site>/gdn_rag/providers.json` | Chat models. **Not** served over HTTP. |
| 4 | `gdn_rag/rag_config/sources.json`, optional `sites/<site>/gdn_rag/sources.json` | Which content sources are on. Merged by `key`. |

Settings are cached for a few seconds and re-read when the file changes.

## Chat providers (`providers.json`)

Copy `gdn_rag/rag_config/providers.example.json` to `sites/<site>/gdn_rag/providers.json`
and edit it.

```json
{
  "chat": [
    { "name": "local", "enabled": true, "priority": 10, "adapter": "openai_compatible",
      "base_url": "http://LOCAL_LLM_HOST:11434/v1", "model": "llama3.1:8b", "api_key": null,
      "timeout": { "connect": 3, "read": 120 }, "max_tokens": 800, "temperature": 0.2 }
  ]
}
```

| Field | Meaning |
|---|---|
| `name` | Short unique name. Used in logs and for the "skip for a while" memory. |
| `enabled` | `false` = ignored. |
| `priority` | Lower number is tried first. |
| `adapter` | `openai_compatible` (OpenAI, Ollama, vLLM, LM Studio, llama.cpp, Gemini's OpenAI endpoint) or `anthropic` (Claude). |
| `base_url` | For `openai_compatible` include the `/v1` part. The code adds `/chat/completions`. |
| `model` | The model name the server knows. |
| `api_key` | See below. `null` = no key (local server). |
| `timeout.connect` | Seconds to wait for a connection. Keep it small for a local server that is sometimes off. |
| `timeout.read` | Seconds to wait between pieces of the answer. |
| `max_tokens`, `temperature` | Passed to the model. |

### API keys

`api_key` can be:

- `null` — no key.
- `"site_config:gdn_rag_gemini_key"` — **recommended**. Read from `site_config.json`.
  Set it with: `bench --site <site> set-config gdn_rag_gemini_key "<the key>"`.
- `"env:MY_VAR"` — read from an environment variable.
- A literal key — works, but a warning is logged. Do not commit real keys.

A provider with a key setting that resolves to nothing is **not ready** and is skipped.
Keys are never returned by an API, never put in HTML or boot info, and are removed from
error messages before they are logged.

### How providers are tried

1. Providers run in order of `priority`.
2. If a provider fails **before** it sends any text, it is skipped for a while
   (60 s, then 120 s, 240 s ... up to 900 s; a rejected key waits 30 minutes) and the next one is tried.
3. If it fails **after** it started answering, the answer ends with "(answer interrupted)".
   We never switch model in the middle of an answer.
4. If every provider is skipped, they are all tried anyway, in priority order.
5. If all fail, the user sees "The assistant is temporarily unavailable."

### Data leaving your server

When a cloud provider (Gemini, OpenAI, Claude) answers, the **question and the retrieved
course text** are sent to that provider. Only the local model keeps data in your network.
Put the local model first if that matters, and disable cloud providers you do not accept.

## Settings (`defaults.json` / `site_config.json["gdn_rag"]`)

Example override in `site_config.json`:

```json
{ "gdn_rag": { "allowed_roles": ["LMS Student"], "shortcut": { "code": "KeyK" }, "chat": { "hourly_limit_per_user": 30 } } }
```

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch. |
| `surfaces.desk` / `surfaces.lms` | `true` | Show the widget in Desk (`/app`) / in the LMS portal (`/lms`). |
| `allowed_roles` | `[]` | Empty = every logged-in user. Otherwise the user needs one of these roles. |
| `shortcut` | Ctrl+Alt+I | `ctrl`, `alt`, `shift`, and `code` (a `KeyboardEvent.code` such as `KeyI`). On some keyboards Ctrl+Alt is AltGr; pick another key if it types a character. |
| `show_launcher` | `true` | The round button. |
| `retrieval.fulltext_candidates` / `all_words_candidates` | `30` / `10` | Candidates from the normal search, and from a second search that needs every question word. Both are then re-ranked. |
| `retrieval.top_k` | `6` | Number of text pieces sent to the model. |
| `retrieval.max_context_chars` | `12000` | Upper limit of text sent to the model. |
| `retrieval.coverage_weight` | `0.6` | How much "how many different question words does the text have" counts, compared to the database's relevance number (which mostly counts repeats). 0 to 1. |
| `retrieval.title_boost` | `0.5` | Extra rank (0 to this value) when the question words are in the title. The text match is scaled to 0..1 first, so a title match alone cannot beat a text that matches every word. |
| `retrieval.short_term_scan_max_rows` | `50000` | The slow scan for 1–2 letter words is skipped on bigger indexes unless one course is selected. |
| `search.aliases` | see file | Words that MariaDB would break (`c++`, `node.js`, `ai`). **Run `gdn-rag reindex` after you change them.** |
| `search.alias_phrases` | see file | Long form of an alias, so "artificial intelligence" also finds `ai`. |
| `search.stopwords` | see file | Words dropped from the **question** only. (The index keeps all words.) |
| `chunking.*` | 1800 / 200 / 80 | Chunk size, overlap and minimum size in characters. Reindex after a change. |
| `chat.history_turns` | `4` | Question/answer pairs sent as memory. |
| `chat.max_question_chars` | `2000` | Longest question. |
| `chat.queue` | `default` | RQ queue for answer jobs. Use a dedicated queue if answers wait behind other jobs. |
| `chat.job_timeout` | `180` | Seconds. Also the lock time of "one question at a time". |
| `chat.hourly_limit_per_user` | `0` | `0` = no limit. |
| `chat.not_found_message` | text | Reply when nothing is found. No model is called in that case. |
| `indexing.realtime` | `true` | Queue a re-index when a watched document is saved. |
| `indexing.scheduled` | `true` | Cron job every 15 minutes catches missed changes. |
| `indexing.max_docs_per_run` | `500` | Limit per scheduled run. The next run continues. |
| `logging.log_questions` | `false` | Put the question text into the usage log. Off by default for privacy. |

## Sources (`sources.json`)

```json
{ "sources": [ { "key": "lms", "enabled": true, "index_mode": "both" } ] }
```

`index_mode`: `realtime` (document events only), `scheduled` (cron only) or `both`.
A source whose app is not installed turns itself off.

## Logs

- `logs/gdn_rag.log` — warnings and errors (provider failures, skipped providers).
- `logs/gdn_rag_usage.log` — one JSON line per question: user, hits, provider, latency, outcome.
  The question text is **not** written unless `logging.log_questions` is `true`.
  On a site log it is in `sites/<site>/logs/`.

## Worker and scheduler

- Answers are background jobs: **a worker must run** (`bench start` includes one).
- Re-index jobs use the `long` queue.
- Your development sites have `disable_scheduler` / `pause_scheduler` set. The 15-minute
  sync does not run there; real-time indexing still works while a worker runs. You can
  always run `bench --site <site> gdn-rag reindex`.
