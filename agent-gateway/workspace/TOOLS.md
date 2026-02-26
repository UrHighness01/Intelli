# Available Tools

## Browser tools
- `GET /tab/snapshot` — Fetch the HTML source, URL, and title of the active tab
- `PUT /tab/snapshot` — (Browser → Gateway) push a new page snapshot
- `GET /tab/inject-queue` — Poll pending addon injections
- `POST /addons` — Create a new addon (name, description, code_js)
- `POST /addons/{name}/activate` — Inject and activate an addon

## Workspace tools
- `GET /workspace/files` — List all workspace files
- `GET /workspace/file?path=<rel>` — Read a workspace file
- `POST /workspace/file?path=<rel>` — Write / create a workspace file
- `DELETE /workspace/file?path=<rel>` — Delete a workspace file
- `GET /workspace/skills` — List skills with metadata
- `POST /workspace/skills` — Create a new skill

## Chat
- `POST /chat/complete` — Chat with context injection support
  - `use_page_context: true` — prepend active tab snapshot as system context
  - `use_workspace: true` — prepend AGENTS.md + SOUL.md as system prompt
