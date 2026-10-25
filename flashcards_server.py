#!/usr/bin/env python3
"""Flashcards MCP server (stdio, JSON-RPC 2.0, Python standard library only).

Education-only study tool. Exposes a local flashcard deck store:

  Tools
    create_card      add a card to a deck (creates the deck if needed)
    list_decks       list available deck files
    list_cards       show every card in one deck (includes answers)
    search_cards     full-text search over fronts and backs
    update_card      edit a card's front or back
    delete_card      remove a card
    draw_quiz        shuffle-draw N cards; answers stay hidden server-side
    reveal_answer    reveal the answer for one drawn question

Transport: line-delimited JSON-RPC on stdin/stdout (MCP stdio flavor).
Run directly, or via an MCP client config, e.g.:

    {"mcpServers": {"flashcards": {
        "command": "python3",
        "args": ["flashcards_server.py"],
        "cwd": "<this folder>"
    }}}

Storage lives in ./data (decks + backups). Deck names are validated so files
always stay inside ./data/decks. For studying, never medical advice.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import flashcards_core as core

SERVER_NAME = "flashcards"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"

BASE_DIR = Path(__file__).resolve().parent
DECKS_DIR = BASE_DIR / "data" / "decks"
BACKUPS_DIR = BASE_DIR / "data" / "backups"


class Database:
    """Per-server state: quiz sessions kept in memory, keyed by session id."""

    def __init__(self, decks_dir: Path = DECKS_DIR, backups_dir: Path = BACKUPS_DIR):
        self.decks_dir = Path(decks_dir)
        self.backups_dir = Path(backups_dir)
        self.sessions: Dict[str, core.QuizSession] = {}

    def load_deck(self, deck_name: str) -> core.Deck:
        return core.Deck.load(deck_name, self.decks_dir)

    def save_deck(self, deck: core.Deck) -> Path:
        return deck.save(self.decks_dir, keep_backups=core.MAX_BACKUPS)

    def new_session_id(self, deck_name: str, seed: str) -> str:
        return f"{deck_name[:24]}-{seed}"


DB = Database()


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------

def _text_result(payload: Dict[str, Any], isError: bool = False) -> Dict[str, Any]:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if isError:
        text = "ERROR: " + text
    return {"content": [{"type": "text", "text": text}], "isError": isError}


def _error_result(kind: str, message: str) -> Dict[str, Any]:
    return _text_result({"error": kind, "message": message}, isError=True)


def _get_str(args: Dict[str, Any], key: str, required: bool = True) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        if required:
            raise ValueError(f"'{key}' must be a non-empty string")
        return ""
    return value.strip()


def _get_optional_str(args: Dict[str, Any], key: str,
                      default: Optional[str] = None) -> Optional[str]:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string if present")
    return value


def _fresh_deck(deck_name: str) -> core.Deck:
    """Load deck if it exists, otherwise create an empty one."""
    try:
        return core.Deck.load(deck_name, DB.decks_dir)
    except core.DeckNotFoundError:
        return core.Deck(deck_name)


def _new_deck_payload(deck: core.Deck) -> Dict[str, Any]:
    return {"deck": deck.name, "card_count": len(deck.cards),
            "file": str(DB.decks_dir / (deck.name + core.DECK_FILE_EXT))}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_create_card(args: Dict[str, Any]) -> Dict[str, Any]:
    deck_name = _get_str(args, "deck_name")
    front = _get_str(args, "front")
    back = _get_str(args, "back")
    deck = _fresh_deck(deck_name)
    card = deck.add_card(front, back)
    DB.save_deck(deck)
    return _text_result({"created": True, "card": card.to_dict(),
                         **_new_deck_payload(deck)})


def tool_list_decks(_args: Dict[str, Any]) -> Dict[str, Any]:
    decks: List[Dict[str, Any]] = []
    for path in core.list_deck_files(DB.decks_dir):
        try:
            deck = core.Deck.load(path.stem, DB.decks_dir)
            decks.append({"deck": deck.name, "card_count": len(deck.cards)})
        except core.DeckNotFoundError:
            continue
    return _text_result({"decks": decks})


def tool_list_cards(args: Dict[str, Any]) -> Dict[str, Any]:
    deck_name = _get_str(args, "deck_name")
    deck = DB.load_deck(deck_name)
    cards = [c.to_dict() for c in deck.cards]
    return _text_result({**_new_deck_payload(deck), "cards": cards,
                         "warning": "This list shows answers; use draw_quiz "
                                    "to study without spoilers."})


def tool_search_cards(args: Dict[str, Any]) -> Dict[str, Any]:
    deck_name = _get_str(args, "deck_name")
    query = _get_str(args, "query")
    deck = DB.load_deck(deck_name)
    hits = core.search_cards(deck, query)
    return _text_result({**_new_deck_payload(deck), "query": query,
                         "match_count": len(hits),
                         "cards": [c.to_dict() for c in hits]})


def tool_update_card(args: Dict[str, Any]) -> Dict[str, Any]:
    deck_name = _get_str(args, "deck_name")
    card_id = _get_str(args, "card_id")
    front = _get_optional_str(args, "front")
    back = _get_optional_str(args, "back")
    deck = DB.load_deck(deck_name)
    card = deck.update_card(card_id, front=front, back=back)
    DB.save_deck(deck)
    return _text_result({"updated": True, "card": card.to_dict(),
                         **_new_deck_payload(deck)})


def tool_delete_card(args: Dict[str, Any]) -> Dict[str, Any]:
    deck_name = _get_str(args, "deck_name")
    card_id = _get_str(args, "card_id")
    deck = DB.load_deck(deck_name)
    card = deck.delete_card(card_id)
    DB.save_deck(deck)
    return _text_result({"deleted": True, "card_id": card.card_id,
                         **_new_deck_payload(deck)})


def tool_draw_quiz(args: Dict[str, Any]) -> Dict[str, Any]:
    deck_name = _get_str(args, "deck_name")
    count = args.get("count", 5)
    if isinstance(count, bool) or not isinstance(count, int):
        if isinstance(count, str) and count.isdigit():
            count = int(count)
        else:
            raise ValueError("'count' must be a positive integer")
    seed = _get_optional_str(args, "seed")
    deck = DB.load_deck(deck_name)
    if not deck.cards:
        raise core.DeckNotFoundError(f"deck '{deck.name}' has no cards to quiz")
    session = core.QuizSession(deck.name,
                               [(c.card_id, c.front, c.back) for c in deck.cards],
                               seed=seed, count=count)
    session_id = DB.new_session_id(deck.name, session.seed)
    DB.sessions[session_id] = session
    return _text_result(session.summary(session_id))


def tool_reveal_answer(args: Dict[str, Any]) -> Dict[str, Any]:
    session_id = _get_str(args, "session_id")
    index = args.get("index")
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("'index' must be an integer")
    session = DB.sessions.get(session_id)
    if session is None:
        raise KeyError(f"no open quiz session with id {session_id!r}; "
                       "run draw_quiz again")
    answer = session.reveal(index)
    return _text_result({"session": session_id, "deck": session.deck_name,
                         "index": index, "answer": answer,
                         "revealed_count": len(session.revealed),
                         "total_questions": session.size()})


TOOLS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "create_card": tool_create_card,
    "list_decks": tool_list_decks,
    "list_cards": tool_list_cards,
    "search_cards": tool_search_cards,
    "update_card": tool_update_card,
    "delete_card": tool_delete_card,
    "draw_quiz": tool_draw_quiz,
    "reveal_answer": tool_reveal_answer,
}


TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "create_card",
        "description": ("Create a flashcard in a local deck. The deck is "
                        "created automatically if it does not exist yet."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deck_name": {"type": "string",
                              "description": "Deck name (letters, digits, "
                                             "spaces, hyphens; max 64 chars)"},
                "front": {"type": "string", "description": "Question side"},
                "back": {"type": "string", "description": "Answer side"},
            },
            "required": ["deck_name", "front", "back"],
        },
    },
    {
        "name": "list_decks",
        "description": "List all flashcard decks in the local store.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_cards",
        "description": ("List every card in a deck, including answers. "
                        "Use draw_quiz instead when the user is studying."),
        "inputSchema": {
            "type": "object",
            "properties": {"deck_name": {"type": "string"}},
            "required": ["deck_name"],
        },
    },
    {
        "name": "search_cards",
        "description": ("Case-insensitive substring search across card "
                        "fronts and backs in one deck."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deck_name": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["deck_name", "query"],
        },
    },
    {
        "name": "update_card",
        "description": "Update the front and/or back of one card by id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deck_name": {"type": "string"},
                "card_id": {"type": "string"},
                "front": {"type": "string"},
                "back": {"type": "string"},
            },
            "required": ["deck_name", "card_id"],
        },
    },
    {
        "name": "delete_card",
        "description": "Delete one card from a deck by id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deck_name": {"type": "string"},
                "card_id": {"type": "string"},
            },
            "required": ["deck_name", "card_id"],
        },
    },
    {
        "name": "draw_quiz",
        "description": ("Draw N shuffled cards from a deck as a study quiz. "
                        "Answers stay on the server; reveal them one at a "
                        "time with reveal_answer. Pass 'seed' to replay the "
                        "exact same draw (deterministic for tests), or omit "
                        "for a fresh shuffle."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deck_name": {"type": "string"},
                "count": {"type": "integer",
                          "description": "How many cards to draw (default 5)"},
                "seed": {"type": "string",
                         "description": "Optional deterministic shuffle seed"},
            },
            "required": ["deck_name", "count"],
        },
    },
    {
        "name": "reveal_answer",
        "description": ("Reveal the answer for one drawn quiz question "
                        "(by session id and question index)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "index": {"type": "integer"},
            },
            "required": ["session_id", "index"],
        },
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------

def handle_rpc(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = request.get("method")
    params = request.get("params") or {}
    req_id = request.get("id")
    notification = req_id is None

    def respond(result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def respond_error(code: int, message: str, data: Any = None) -> Dict[str, Any]:
        err_obj: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err_obj["data"] = data
        return {"jsonrpc": "2.0", "id": req_id, "error": err_obj}

    if method == "initialize":
        return respond({
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Flashcard study server. Education only, not medical advice. "
                "Study flow: create_card ... then draw_quiz, and call "
                "reveal_answer one question at a time."
            ),
        })

    if method == "ping":
        return respond({})

    if method in ("notifications/initialized", "initialized", "cancel"):
        return None

    if method == "tools/list":
        return respond({"tools": TOOL_SCHEMAS})

    if method == "tools/call":
        return _dispatch_tool_call(params, respond, respond_error)

    return respond_error(-32601, f"Method not found: {method}")


def _dispatch_tool_call(params, respond, respond_error):
    name = params.get("name")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return respond_error(-32602, "arguments must be an object")
    if name not in TOOLS:
        return respond_error(-32602, f"Unknown tool: {name}")
    try:
        result = TOOLS[name](args)
        return respond(result)
    except (core.DeckNotFoundError, core.CardNotFoundError) as exc:
        return respond(_error_result(type(exc).__name__, str(exc.args[0])))
    except core.DeckNameError as exc:
        return respond(_error_result("DeckNameError", str(exc)))
    except ValueError as exc:
        return respond(_error_result("ValueError", str(exc)))
    except OSError as exc:
        return respond(_error_result("OSError", f"filesystem error: {exc}"))
    except Exception as exc:  # noqa: BLE001 - keep server alive
        return respond(_error_result("InternalError",
                                     f"unexpected error: {exc!r}"))


def write_message(message: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(read_line: Callable[[], str], write_line) -> None:
    while True:
        line = read_line()
        if not line:
            return
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32600,
                                      "message": "Request must be an object"}}
            else:
                response = handle_rpc(request)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            write_line(response)


def main() -> None:
    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    serve(sys.stdin.readline, write_message)


if __name__ == "__main__":
    main()
