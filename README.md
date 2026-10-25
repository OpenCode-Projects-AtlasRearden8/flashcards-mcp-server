# flashcards-mcp-server

A small, local-first MCP (Model Context Protocol) server for agent-assisted
studying. Create flashcards with your coding assistant, search your decks,
and quiz yourself: the agent draws shuffled cards, but the answers stay on
your side of the table until you reveal them, one at a time.

By [Asdale Dynamics, LLC](https://dylanasdale.github.io) - plain-English AI
education, tools, and books. Education only; never medical advice.

## Why it works for studying

Normal "quiz me" flows leak the answer into the model's context, so it is
very easy for an AI to nudge you toward the answer you have not recalled
yet. This server holds the answers **in memory on the server**: the agent
sees the questions, you recall out loud, and only then does the agent call
`reveal_answer` for one card at a time - and compare what you said.

## Features

- **Python standard library only** - no pip install, no API keys, no network.
- **MCP stdio transport** - line-delimited JSON-RPC 2.0 on stdin/stdout, the
  same pattern used by notes-mcp-server and agent-skill-starter.
- **JSON storage** - each deck is one human-readable file under `data/decks/`.
- **Path safety** - deck names are validated so every file write stays inside
  the decks directory (no `..`, no separators, no absolute paths).
- **Auto-backup** - before any deck edit, the previous file is copied to
  `data/backups/<deck>.<timestamp>.bak.json` (newest 5 kept).
- **Shuffled draw + reveal** - `draw_quiz` seeds a keyed shuffle; answers are
  never returned in the draw. `reveal_answer` shows one card at a time.
- **Deterministic shuffles** - pass the same `seed` and you get the same
  order, which makes the behavior testable and replayable.
- **Sample deck included** - `sample_decks/study_demo_deck.json`.

## The deck format

```json
{
  "name": "Study Demo Deck",
  "cards": [
    {"id": "exam_sample01", "front": "Question...", "back": "Answer..."}
  ]
}
```

Card `id`s that you leave off or blank are filled in automatically. Files are
written atomically (temp file + rename), so a crash mid-write cannot corrupt
a deck.

## Tools

| Tool | What it does |
| --- | --- |
| `create_card` | Add a card to a deck (creates the deck file on first use) |
| `list_decks` | List all decks with card counts |
| `list_cards` | Dump a whole deck including answers (not for studying) |
| `search_cards` | Case-insensitive search across fronts and backs |
| `update_card` | Edit a card's front and/or back by id |
| `delete_card` | Remove a card by id |
| `draw_quiz` | Shuffle-draw N cards; returns questions only |
| `reveal_answer` | Show one drawn answer, by session id and index |

## Quick start (terminal smoke test)

```bash
cd flashcards-mcp-server

# 1. Handshake
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
| python3 flashcards_server.py | python3 -c "import json,sys; print(json.loads(sys.stdin.readline())['result']['serverInfo'])"

# 2. List tools (send initialize first, then tools/list)
{
  printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
  printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
} | python3 flashcards_server.py | tail -n 1
```

## Using it with an MCP client

Add the server to your client's MCP config (the `mcpServers` block used by
opencode and similar tools):

```json
{
  "mcpServers": {
    "flashcards": {
      "command": "python3",
      "args": ["/absolute/path/to/flashcards-mcp-server/flashcards_server.py"]
    }
  }
}
```

Then install the sample deck by copying it (or just ask the agent to run
these commands):

```bash
mkdir -p data/decks
cp sample_decks/study_demo_deck.json data/decks/
```

Or let the agent build your deck for you: "Create a deck called Licensing
Basics with five cards about MIT vs Apache-2" and it will call `create_card`
eight times, as needed.

Suggested study prompt for the agent:

> Use create_card to build my deck if it does not exist, then call draw_quiz
> with count 5. For each question in order, let me answer out loud before
> you call reveal_answer with that index. Grade me softly and explain the
> ones I missed.

## Layout

```
flashcards-mcp-server/
  flashcards_server.py    MCP stdio server (JSON-RPC 2.0)
  flashcards_core.py      deck storage, path safety, backups, quiz sessions
  flashcards_errors.py    shared error types
  sample_decks/           starter deck you can copy into data/decks/
  test_flashcards.py      unit tests (round-trips, deterministic shuffles, security)
  data/                   created at runtime: data/decks/, data/backups/
```

## Testing

```bash
python3 -m unittest test_flashcards.py -v
```

Covers: JSON round-trips (save/load, atomic writes), auto-backup creation
and pruning, shuffle determinism (same seed -> same order; different seed ->
(order usually) different), quiz draw / reveal flows, path traversal and
injection rejections, and unknown-tool handling.

## Privacy and safety

- Everything stays on your machine. No network calls, no keys, no telemetry.
- Deck contents are only as private as your disk, so treat `data/` like any
  personal notes folder.
- Education only - never medical advice, even when the flashcards are.

## License

MIT - Copyright (c) 2026 Asdale Dynamics, LLC. See `LICENSE`.
