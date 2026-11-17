### GDN RAG

A chat helper that answers questions **only from your indexed content**, with links to the sources.
v1 indexes the courses and lessons of the **Frappe LMS** app. It does not change any other app.

- Opens with **Ctrl + Alt + I** or a round button at the bottom right (in `/lms` and in Desk).
- A floating window you can **move** and **resize**. It can also dock to the right side.
- Knows the course or lesson that is open. Follows the LMS access rules (enrolled, instructor, moderator).
- Tries your AI models one by one (local server first, then Gemini / OpenAI / Claude ...).
- Needs no DocTypes: settings are JSON files. Works on MariaDB 10.11 (keyword search, no vector database).
- Plug-in sources: other apps (CRM ...) can add content later without changing the core.

### Install (Frappe / ERPNext v16, branch `version-16-develop`)

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app gdn_rag https://github.com/erp-gdnint/gdn_rag --branch version-16-develop   # needs the lms app
bench --site <site> install-app gdn_rag
bench --site <site> migrate
bench --site <site> gdn-rag status
```

### Documentation (in `gdn_rag/customisations/docs/`)

| File | For |
|---|---|
| `developer-guide.md` | Start here. Simple English. Setup, models, commands, problems and fixes. |
| `configuration.md` | All settings, `providers.json`, keys, logs. |
| `adding-a-source.md` | How to add another content source. |
| `plan.md` | Design, decisions, and what was built (section 19). |

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/gdn_rag
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
