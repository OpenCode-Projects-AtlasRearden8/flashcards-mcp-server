# STATUS.md - flashcards-mcp-server

Built and verified on: 2026-10-25
License: MIT - Copyright (c) 2026 Asdale Dynamics, LLC

## What was built

A small, dependency-free MCP server (Python 3 standard library only, stdio
transport) for agent-assisted flashcard studying:

- **flashcards_server.py** - MCP stdio server: line-delimited JSON-RPC 2.0
  on stdin/stdout. Implements `initialize`, `notifications/initialized`,
  `ping`, `tools/list`, `tools/call`. Eight tools: `create_card`,
  `list_decks`, `list_cards`, `search_cards`, `update_card`, `delete_card`,
  `draw_quiz`, `reveal_answer`.
- **flashcards_core.py** - deck model + storage, path-safe resolution,
  atomic saves (temp file + rename), auto-backup with pruning, keyed
  deterministic shuffle for quiz draws, in-memory quiz sessions.
- **flashcards_errors.py** - shared error types.
- **sample_decks/study_demo_deck.json** - 5-card starter deck.
- **test_flashcards.py** - 51 unit tests.
- **README.md** - usage, MCP client config, study prompt, testing guide.
- **LICENSE** - MIT, Asdale Dynamics, LLC.

Key design points:

- **Anti-spoiler quiz flow**: `draw_quiz` returns questions only (no
  answer fields); answers live in a server-side session, and the agent
  calls `reveal_answer(session_id, index)` one card at a time while the
  learner recalls out loud first.
- **Deterministic shuffles**: quiz order is a keyed shuffle (cards sorted
  by a seeded random key). Same deck + same `seed` => identical order and
  identical reveal sequence; draws of N are a prefix of the full draw.
- **Path safety**: deck names are validated (no `/`, `\`, `:`, no `..`,
  no leading dot, 64-char cap) and every resolved path must stay inside
  `data/decks/`; backups stay inside `data/backups/`.
- **Auto-backup**: before overwriting a deck, the old file is copied to
  `data/backups/<deck>.<timestamp>.bak.json`; newest 5 kept.
- **Canonical names**: the filename stem is the deck's canonical name, so
  a deck round-trips to the same file even if the JSON `name` metadata
  field differs (this split-brain bug was found and fixed during testing).

## How to run it

```bash
cd flashcards-mcp-server    # this folder
python3 flashcards_server.py   # speaks JSON-RPC 2.0 on stdin/stdout
```

With an MCP client (opencode-style `mcpServers` config):

```json
{"mcpServers": {"flashcards": {
  "command": "python3",
  "args": ["/absolute/path/to/flashcards-mcp-server/flashcards_server.py"]
}}}
```

Install the sample deck:

```bash
mkdir -p data/decks && cp sample_decks/study_demo_deck.json data/decks/
```

## Verification results (all passing)

- `python3 -m unittest test_flashcards.py -v` -> **51 tests, OK**
  - round-trips: save/load equality, card ids preserved, update/delete
    round-trips, valid JSON on disk, no stray temp files
  - backups: created on overwrite, pruned to max, none on first save,
    backups confined to the backups directory
  - shuffle determinism: same seed -> same order, same questions and
    same reveal sequence; different seeds -> different orders; random
    seed drawn when omitted; prefix property of partial draws verified;
    bad count values rejected
  - security: `../evil`, nested traversal, slashes, backslashes, colons,
    `.hidden`, `.`, `..`, overlong names, special characters all rejected
    with no files written at the escaped locations; corrupt deck JSON
    surfaces as a clean ValueError; unknown tools rejected
  - protocol: initialize handshake (protocolVersion 2025-06-18),
    tools/list matches the dispatch table, notifications get no reply,
    unknown method -> JSON-RPC error -32601, bad tool arguments ->
    `isError: true` result (server never crashes)
- End-to-end stdio smoke test (subprocess): initialize, tools/list,
  create_card, draw_quiz with answers hidden, reveal_answer with a real
  session id, traversal attempt blocked, unknown tool rejected; stderr
  clean; disk showed only files inside `data/decks`.
- Editing the sample deck via the server wrote one auto-backup and
  round-tripped correctly into the same file.
- `python3 -m py_compile` on all sources: OK.
- Committed locally as the existing git identity; never pushed.

## Known gaps / limits

- Single-process, single-user: quiz sessions live in memory, so they end
  when the server stops; multi-step quizzes assume the client keeps one
  server process alive.
- Sessions have no TTL, so long-lived hosts could accumulate idle
  sessions (harmless memory; worth adding reuse limits if decks get big).
- No spaced repetition / scheduling (SRS), no due dates, no stats.
- Search is plain case-insensitive substring matching, no ranking.
- No concurrent-writer locking across separate processes editing the same
  deck file (atomic per-write, last-writer-wins otherwise).
- Only `protocolVersion` "2025-06-18" is advertised for the handshake;
  an untested client may want a different negotiated version.
- Windows-tested paths only in theory: separators are rejected, so deck
  names are portable, but the tool was exercised on Linux in this session.
