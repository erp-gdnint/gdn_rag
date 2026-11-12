# GDN RAG — Developer Guide

This guide is for developers. It uses simple English.
Read it from top to bottom the first time.

---

## 1. What is GDN RAG?

GDN RAG is a **chat helper** for the LMS.

A user asks a question. The helper looks in the course text. Then an AI model writes
the answer. The helper shows the lessons where the answer comes from.

**RAG** means: first **find** the right text, then ask the AI to **answer** from that text.
The AI does not guess. If the text has no answer, the helper says so.

The app name is `gdn_rag`. It does **not** change any other app.

---

## 2. What can it do?

- A chat window opens with **Ctrl + Alt + I**.
- You can also click the **round button** at the bottom right.
- You can **move** the window. Drag the top bar.
- You can **change the width and the height**. Drag an edge or a corner.
- You can **dock** the window to the right side. Click the dock button.
- It knows the course or lesson that the user has open.
- The user can pick another course, or "All courses".
- It answers only from the course text. It shows the **sources**.
- It uses many AI models. It tries them **one by one**. The first one that works answers.
- It works in the LMS (`/lms`) and in Desk (`/app`).
- If the LMS app is not installed, nothing breaks. The chat just does not show.

---

## 3. How does it work?

There are two jobs: **index** and **answer**.

```
INDEX (done in the background)

  Course / Lesson  --->  cut into small pieces (chunks)  --->  saved in a table
  (LMS data)                                                   __gdn_rag_chunk


ANSWER (when the user asks)

  User question
       |
       v
  1. Find the user's page (which course? which lesson?)
  2. Search the table for the best chunks
       - only chunks that THIS user may see
  3. Nothing found?  -->  say "not found". Do NOT call an AI model.
  4. Send question + chunks to an AI model (local first, then others)
  5. Show the answer + links to the lessons
```

**Why a table and not a DocType?** You said: no DocTypes for now. The chunks are saved
in a normal SQL table. The settings are JSON files. You can make DocTypes later.

**Why is it light for the server?**

- No AI model runs on the Frappe server.
- Answers run in a **background job**. The web server is free.
- The index changes only when a lesson changes.
- Text that did not change is not saved again.

---

## 4. Before you start

You need:

- Frappe v15 and the LMS app (for LMS content)
- MariaDB **10.11** or newer (this is your production version)
- Redis
- A **worker** (`bench start` starts one)

MariaDB 10.11 has no vector search. So this app uses **keyword search**. It is made
to work well (see section 14).

---

## 5. Install

```bash
cd ~/gdn-bench
bench --site YOUR_SITE install-app gdn_rag     # skip if it is already installed
bench --site YOUR_SITE migrate
bench --site YOUR_SITE gdn-rag status
```

In the `status` output, check this line:

```
Stop-word probe : ok
```

If you see `failed`, run:

```bash
bench --site YOUR_SITE gdn-rag rebuild-index
```

**After you pull new code later**, run:

```bash
bench --site YOUR_SITE migrate
bench --site YOUR_SITE clear-cache
```

Then restart the web server and the worker. You do not need `bench build`. You do not need to
force-refresh the browser: the chat script has a version in its address, and the version changes
when the file changes. (Browsers keep static files for 12 hours, so this is needed.)

---

## 6. Add AI models

You need a file with your models. It is **not** in the app. It is in your site folder.

**Step 1.** Copy the example file.

```bash
mkdir -p sites/YOUR_SITE/gdn_rag
cp apps/gdn_rag/gdn_rag/rag_config/providers.example.json sites/YOUR_SITE/gdn_rag/providers.json
```

**Step 2.** Open `sites/YOUR_SITE/gdn_rag/providers.json` and change it.

```json
{
  "chat": [
    {
      "name": "local",
      "enabled": true,
      "priority": 10,
      "adapter": "openai_compatible",
      "base_url": "http://192.168.1.50:11434/v1",
      "model": "llama3.1:8b",
      "api_key": null,
      "timeout": { "connect": 3, "read": 120 }
    },
    {
      "name": "gemini",
      "enabled": true,
      "priority": 20,
      "adapter": "openai_compatible",
      "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
      "model": "gemini-2.5-flash",
      "api_key": "site_config:gdn_rag_gemini_key"
    }
  ]
}
```

What the words mean:

| Word | Meaning |
|---|---|
| `priority` | A small number goes first. `10` is before `20`. |
| `enabled` | `false` means "do not use". |
| `adapter` | `openai_compatible` works with Ollama, vLLM, LM Studio, OpenAI and Gemini. Use `anthropic` for Claude. |
| `base_url` | The address of the model server. |
| `model` | The model name. |
| `api_key` | `null` means no key (a local server). |
| `timeout.connect` | How many seconds to wait for the server. Use a small number for your local server. It is sometimes off. |

**Step 3.** Put the secret keys in `site_config.json`. **Never** write a key in `providers.json`.

```bash
bench --site YOUR_SITE set-config gdn_rag_gemini_key "PUT_THE_KEY_HERE"
```

**Step 4.** Test the models.

```bash
bench --site YOUR_SITE gdn-rag test-providers
```

You see `OK` or `FAIL` for each model.

**What happens when a model is off?**

1. The helper tries the first model.
2. It cannot connect. It marks the model as "down".
3. It tries the next model. The user gets an answer.
4. The next question skips the "down" model for 1 minute. Then 2, 4, 8 ... up to 15 minutes.
   So the user does not wait again.

If a model stops **in the middle** of an answer, the helper does **not** change model.
It ends the answer and writes "(answer interrupted)".

> **Privacy:** when a cloud model (Gemini, OpenAI, Claude) answers, the question and the
> course text go to that company. Only your local model keeps data inside your network.

---

## 7. Make the index

```bash
bench --site YOUR_SITE gdn-rag reindex
```

This reads all courses and lessons. It can take some time.

After that, the index stays fresh **by itself**:

- When someone saves a course, chapter or lesson, a background job updates it.
- Every 15 minutes, a scheduled job checks for missed changes.

Your dev sites have the scheduler turned off (`disable_scheduler`). Then only the
real-time update works. You can always run `reindex` by hand.

Reindex one course only:

```bash
bench --site YOUR_SITE gdn-rag reindex --course COURSE_NAME
```

---

## 8. Try it in the terminal

Show only the search result (no AI model is called):

```bash
bench --site YOUR_SITE gdn-rag ask "how do I get a certificate" --dry-run
```

Ask a real question:

```bash
bench --site YOUR_SITE gdn-rag ask "what is a variable?" --user student@example.com
```

Pretend the user is on a lesson page:

```bash
bench --site YOUR_SITE gdn-rag ask "summarize this lesson" --path /lms/courses/my-course/learn/1-2
```

The `--user` option is important. The answer depends on **who** asks.

---

## 9. Use the chat window

1. Open an LMS page (`/lms/...`), or a Desk page.
2. Press **Ctrl + Alt + I**, or click the round button.
3. Type a question. Press **Enter**. (**Shift + Enter** makes a new line.)
4. Click **Stop** if you want to stop the answer.
5. Press **Esc** to close.

**Move and resize:**

| You want to | You do this |
|---|---|
| Move the window | Drag the top bar |
| Change the width | Drag the left or right edge |
| Change the height | Drag the top or bottom edge |
| Change both | Drag a corner |
| Dock to the right side | Click the dock button (the box with a line) |
| Float again | Click the same button again |

The browser remembers the size and the place.
On a phone (narrow screen) the window uses the full screen.

**The line under the title** shows the search area:

- **This page**: the course that is open (and the lesson, if one is open).
- **All courses**: every course the user can see.
- **A course from the list**: search by name.

**New chat** (the plus button) clears the talk.
The talk is kept only in the browser tab. It is **not** saved on the server.

---

## 10. Who can see what?

The helper follows the LMS rules. The rules are checked **when the user asks**.
So a change in enrollment works at once (after at most 5 minutes, see below).

| User | Can use |
|---|---|
| Any logged-in user | The course page text and the outline of **published** courses. The free-preview lessons. |
| Enrolled student | Everything in the courses he or she joined. |
| Course instructor | Everything in his or her own courses (also if not published). |
| Moderator and Administrator | Everything. |
| System Manager (without the Moderator role) | Same as any user. |

**Never used:** instructor notes, instructor content, quizzes, questions, answers and
assignments. The helper does not read them. They are not in the table.

> **Good to know:** in LMS 2.44 the lesson page shows the "Instructor Notes" field to every
> learner. Only "Instructor Content" is hidden from learners. This helper reads **neither** of
> them. That is on purpose: it is the safe choice. If you want the helper to use the notes
> that learners can see, that is a small change in `sources/lms.py` (`LESSON_FIELDS`). Talk to
> the team first.

Guests (not logged in) cannot use the chat.

The access rules are saved for 5 minutes for each user. This keeps the server fast.

---

## 11. Settings

Default settings are in `gdn_rag/rag_config/defaults.json`.
To change a setting for one site, put it in `site_config.json` under the key `gdn_rag`.

Example:

```json
{
  "gdn_rag": {
    "allowed_roles": ["LMS Student", "Course Creator"],
    "shortcut": { "code": "KeyK" },
    "chat": { "hourly_limit_per_user": 30 },
    "surfaces": { "desk": false }
  }
}
```

The most useful settings:

| Setting | What it does |
|---|---|
| `enabled` | `false` turns the whole helper off. |
| `allowed_roles` | Only these roles can use the chat. Empty means all logged-in users. |
| `surfaces.lms` / `surfaces.desk` | Show the chat in `/lms` or in `/app`. |
| `shortcut` | Change the key. Some keyboards use Ctrl+Alt as "AltGr". Then pick another key. |
| `chat.hourly_limit_per_user` | Max questions per hour. `0` means no limit. |
| `chat.not_found_message` | The text when nothing is found. |
| `logging.log_questions` | `true` writes the question text to the log. It is `false` for privacy. |

All settings are explained in `configuration.md`.

---

## 12. Where is the code?

All files are in `apps/gdn_rag/gdn_rag/`.

| Path | What it is |
|---|---|
| `hooks.py` | Connects everything to Frappe. |
| `sources/base.py` | The **plug-in interface**. Every content source follows it. |
| `sources/lms.py` | The LMS source. All LMS rules are here. |
| `rag/db.py` | The SQL table and all SQL. |
| `rag/normalize.py` | Makes text ready for search (short words, `C++`, word endings). |
| `rag/text.py`, `rag/chunking.py` | Turn lesson content into small text pieces. |
| `rag/indexer.py`, `rag/events.py`, `rag/jobs.py` | Keep the index fresh. |
| `rag/retriever.py` | Search and access check. |
| `rag/access.py` | Makes the "who can see this" SQL. |
| `rag/llm/` | AI model adapters and the "try one by one" logic (`chain.py`). |
| `rag/answer.py` | The whole answer flow. |
| `api/chat.py` | The web API for the chat window. |
| `web/inject.py` | Puts the chat script into `/lms` pages. |
| `web/boot.py` | Gives settings to Desk pages. |
| `public/js/gdn_rag_widget.js` | The chat window (one file, no build step). |
| `public/js/gdn_rag_loader.js` | A tiny file for Desk. It loads the chat window with a version in the address. |
| `rag/log.py` | Loggers with the right level (Frappe hides warnings on a production server). |
| `commands/__init__.py` | The `bench gdn-rag ...` commands. |
| `rag_config/*.json` | Default settings and the example models file. |
| `tests/` | Tests. |

**How does the chat appear in the LMS?**
The LMS page is a separate Vue app. It ignores the normal Frappe script settings.
So `web/inject.py` adds one `<script>` line to the page before it is sent. It does this
only for logged-in users on `/lms` pages. It does not change the LMS app.

---

## 13. Run the tests

**Small tests** (no site needed, a few seconds):

```bash
cd /tmp
~/gdn-bench/env/bin/python -m unittest gdn_rag.tests.test_normalize gdn_rag.tests.test_text gdn_rag.tests.test_chain gdn_rag.tests.test_inject gdn_rag.tests.test_log gdn_rag.tests.test_indexer_queue
```

**Tests with a real database** (LMS must be installed on the site):

```bash
cd ~/gdn-bench
bench --site YOUR_TEST_SITE set-config allow_tests true
bench --site YOUR_TEST_SITE run-tests --app gdn_rag
```

> **Warning:** these tests create and delete LMS courses and users whose names start with
> `gdnragtest`, and they commit to the database. Use a **test site**, never your real site.

**Chat window text tests** (need Node.js). They check that model text can never create HTML:

```bash
node apps/gdn_rag/gdn_rag/tests/widget_markdown.test.js
```

**Speed check** (on a test site). It adds 6000 fake chunks, asks 300 questions, and removes them:

```bash
cd ~/gdn-bench/sites
../env/bin/python ../apps/gdn_rag/gdn_rag/tests/bench_retrieval.py YOUR_TEST_SITE
```

On the development machine the result was about 28 ms (p95) for 6000 chunks.

> Tests were run on MariaDB **11.8**. Your production MariaDB is **10.11**. The SQL in this
> app only uses features that 10.11 has, but please also run the tests once on a 10.11 test site.

---

## 14. Why is search made this way?

MariaDB full-text search has four problems. The app fixes each one.

| Problem | Example | Fix |
|---|---|---|
| Words with 1 or 2 letters are not searched | `AI`, `JS`, `UI` | A list of **aliases** (`ai` becomes `artificialintelligence`). Other short words use a small, limited scan. |
| Common words are ignored | `about`, `who`, `will` | The index is created with this feature **off**. The app removes useless words from the **question** itself. |
| Symbols are cut | `C++` becomes `C` | Aliases: `c++` becomes `cplusplus`. |
| No word endings | `certificate` does not find `certification` | The app cuts the ending and searches the start of the word (`certific*`). |

Add your own aliases in `defaults.json` (`search.aliases`). Then run `reindex`.

**Why not just use `LIKE '%word%'`?**
It reads every row, it cannot rank the results, and it finds words inside other words
(`art` inside `start`). The app uses `LIKE`-style search only for 1 and 2 letter words, and
only on a limited number of rows.

**Later:** meaning search (embeddings) needs MariaDB 11.7+ or extra code. The design allows
it, but it is not in v1.

---

## 15. Problems and fixes

| Problem | Why | Fix |
|---|---|---|
| The chat button does not show in `/lms` | The user is a guest, the LMS app is missing, or the setting is off | Log in. Run `gdn-rag status` and check "Sources". Check `enabled`, `surfaces.lms`, `allowed_roles`. |
| The chat button does not show in Desk | Same as above | Same. Then reload the page with Ctrl+Shift+R. |
| "The assistant is not configured yet." | No `providers.json`, or no model is enabled | Do section 6. |
| The answer never comes. It says "Waiting for a background worker..." | No worker is running | Run `bench start` or `bench worker`. |
| "The assistant is temporarily unavailable." | Every model failed | Run `gdn-rag test-providers`. Check keys and addresses. |
| "I couldn't find this in the course material." | No chunk matched, or the user may not see the content | Run `gdn-rag ask "..." --dry-run --user THAT_USER`. If hits are 0, run `reindex`. |
| A new lesson is not found | The background job did not run | Check the worker. Run `reindex --course NAME`. |
| A student cannot find a lesson of a course he just joined | Access rules are saved for 5 minutes | Wait 5 minutes. |
| `Stop-word probe: failed` | The index has the default stop words | `gdn-rag rebuild-index` |
| Search for `C++` finds nothing | The index is older than the alias | `gdn-rag reindex` |
| Ctrl+Alt+I types a letter | Your keyboard uses AltGr | Change `shortcut.code` in settings. |
| `Please wait for the current answer to finish.` | One question at a time for each user | Wait, or click Stop. |
| The chat window looks old after an update | The browser keeps a copy of the script | Run `migrate` and `clear-cache` (section 5), then reload the page. |
| The first question is slow, the next ones are fast | The first model is off. The helper waits for it once (`timeout.connect`), then skips it for a while | Use a small `timeout.connect` for the local model. |
| `gdn_rag.log` is empty | Nothing went wrong | Good. Warnings (for example a model that is off) go there. |

**Logs** (in `sites/YOUR_SITE/logs/`, and the same lines in `logs/` of the bench):

- `gdn_rag.log`: warnings and errors (for example "provider local failed ...")
- `gdn_rag_usage.log`: one line for each question: user, number of hits, model, time, result.
  The question text is **not** written (see `logging.log_questions`).

---

## 16. Rules for developers

1. Change **only** `gdn_rag`. Never edit LMS, Frappe or other apps.
2. Do not write secrets in code or in `providers.json`. Use `site_config:`.
3. Every SQL value goes in a **parameter**. Never build SQL from user text.
4. Access is checked in SQL **before** text goes to a model. Do not change this.
5. Never index private fields (instructor notes, quiz answers).
6. Every hook must catch its errors. A bug here must not break a save or a page.
7. Use only SQL that MariaDB 10.11 knows.
8. The chat window must escape all model text. Never use raw HTML from a model.
9. Style: tabs, double quotes (see `pyproject.toml`).

---

## 17. Add a new source (for example CRM)

Read `adding-a-source.md`. In short:

1. Write a class that follows `sources/base.py`.
2. Add its path to the `gdn_rag_sources` hook of your app.
3. Add one line to `sources.json`.
4. Run `gdn-rag reindex --source YOUR_KEY`.

The core code does not change.

---

## 18. Small dictionary

| Word | Meaning |
|---|---|
| **RAG** | Find text first, then let an AI answer from it. |
| **Chunk** | A small piece of text (about one screen). |
| **Index** | The saved chunks that we search. |
| **Source** | A kind of content, for example the LMS. |
| **Scope** | The area of a search, for example one course. |
| **Provider** | An AI model service (local, Gemini, OpenAI, Claude). |
| **Fallback** | Use the next model when the first one fails. |
| **Alias** | A short name that stands for a longer word (`ai` for `artificialintelligence`). |
| **Stop word** | A very common word (`the`, `about`). |
| **Citation** | A link that shows where the answer comes from. |
| **Worker** | A background process that runs jobs. |
