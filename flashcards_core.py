"""Core logic for the flashcards MCP server.

Standard library only. Handles deck storage as JSON files, path safety,
auto-backup, card CRUD, search, and shuffled quiz draw / answer reveal.

Decks live at data/decks/<name>.json with the shape:

    {"name": "...", "cards": [{"id": "...", "front": "...", "back": "..."}]}

Backups go to data/backups/<name>.<timestamp>.bak.json (newest kept).
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import flashcards_errors as err

DECK_FILE_EXT = ".json"
DECKS_DIR = Path("data") / "decks"
BACKUPS_DIR = Path("data") / "backups"
MAX_BACKUPS = 5
NAME_MAX_LEN = 64
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _\-]{0,%d}[A-Za-z0-9]*" % 62)
SEED_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DeckNameError(err.DeckNameError):
    pass


class DeckNotFoundError(err.DeckNotFoundError):
    pass


class CardNotFoundError(err.CardNotFoundError):
    pass


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def validate_deck_name(deck_name: str) -> str:
    """Validate a deck name and return it, or raise DeckNameError.

    Rules:
      - non-empty, <= 64 chars
      - only letters, digits, spaces, underscores, hyphens
      - must start with a letter or digit (no leading dots)
      - no path separators ("", "\\\\") or colons, no "." or ".."
    """
    if not isinstance(deck_name, str):
        raise DeckNameError("deck name must be a string")
    name = deck_name.strip()
    if not name:
        raise DeckNameError("deck name must not be empty")
    if len(name) > NAME_MAX_LEN:
        raise DeckNameError("deck name must be 64 characters or fewer")
    if "/" in name or "\\" in name or ":" in name:
        raise DeckNameError("deck name must not contain path separators or colons")
    if name in (".", ".."):
        raise DeckNameError("deck name must not be '.' or '..'")
    if not NAME_RE.fullmatch(name):
        raise DeckNameError(
            "deck name may only contain letters, digits, spaces, "
            "underscores, hyphens, and must start with a letter or digit"
        )
    return name


def resolve_deck_path(deck_name: str, decks_dir: Optional[Path] = None) -> Path:
    """Resolve the deck file path, ensuring it stays inside decks_dir."""
    if decks_dir is None:
        decks_dir = DECKS_DIR
    name = validate_deck_name(deck_name)
    root = Path(decks_dir).resolve()
    candidate = (root / (name + DECK_FILE_EXT)).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise DeckNameError("deck path escapes the decks directory")
    if candidate.suffix != DECK_FILE_EXT:
        raise DeckNameError("unexpected deck file extension")
    return candidate


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

def uuid_hex12() -> str:
    return uuid.uuid4().hex[:12]


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


@dataclass
class Card:
    card_id: str
    front: str
    back: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Card":
        if not isinstance(data, dict):
            raise ValueError("card entry must be a JSON object")
        for key in ("front", "back"):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"card field '{key}' must be a non-empty string")
        card_id = data.get("id")
        return cls(card_id=card_id if isinstance(card_id, str) and card_id.strip()
                   else uuid_hex12(),
                   front=data["front"].strip(),
                   back=data["back"].strip())

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.card_id, "front": self.front, "back": self.back}


class Deck:
    def __init__(self, name: str, cards: Optional[List[Card]] = None):
        self.name = validate_deck_name(name)
        self.cards: List[Card] = list(cards or [])

    @classmethod
    def from_dict(cls, data: Dict[str, Any], name_override: Optional[str] = None) -> "Deck":
        if not isinstance(data, dict):
            raise ValueError("deck file must contain a JSON object")
        name = name_override if name_override is not None else data.get("name")
        cards_raw = data.get("cards", [])
        if not isinstance(cards_raw, list):
            raise ValueError("deck 'cards' must be a list")
        return cls(name=name, cards=[Card.from_dict(c) for c in cards_raw])

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "cards": [c.to_dict() for c in self.cards]}

    # -- persistence --------------------------------------------------------
    @classmethod
    def load(cls, deck_name: str, decks_dir: Path = None) -> "Deck":
        path = resolve_deck_path(deck_name, decks_dir)
        if not path.exists():
            raise DeckNotFoundError(deck_name)
        with open(path, "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ValueError(f"deck file is not valid JSON: {exc}") from exc
        try:
            # Canonical deck name is the lookup stem (the filename), so a
            # deck loaded as "my_deck.json" also saves back to "my_deck.json"
            # even if the JSON's "name" metadata field reads differently.
            return cls.from_dict(data, name_override=deck_name)
        except (ValueError, DeckNameError) as exc:
            caught = exc
        raise DeckNameError(
            f"deck file {path.name} does not parse as a valid deck: {caught}"
        )

    def save(self, decks_dir: Path = None, keep_backups: int = MAX_BACKUPS) -> Path:
        """Persist the deck atomically, backing up any existing file first."""
        decks_dir = Path(DECKS_DIR if decks_dir is None else decks_dir)
        decks_dir.mkdir(parents=True, exist_ok=True)
        path = resolve_deck_path(self.name, decks_dir)
        backup_deck_if_exists(path, keep_backups=keep_backups)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return path

    @classmethod
    def delete(cls, deck_name: str, decks_dir: Path = None) -> None:
        path = resolve_deck_path(deck_name, decks_dir)
        if not path.exists():
            raise DeckNotFoundError(deck_name)
        path.unlink()

    # -- card CRUD ----------------------------------------------------------
    def add_card(self, front: str, back: str, card_id: Optional[str] = None) -> Card:
        if not isinstance(front, str) or not isinstance(back, str):
            raise ValueError("front and back must be strings")
        if not front.strip() or not back.strip():
            raise ValueError("front and back must both be non-empty")
        card = Card(card_id=card_id or uuid_hex12(),
                    front=front.strip(), back=back.strip())
        if any(c.card_id == card.card_id for c in self.cards):
            raise ValueError(f"duplicate card id: {card.card_id}")
        self.cards.append(card)
        return card

    def get_card(self, card_id: str) -> Card:
        cid = str(card_id)
        for card in self.cards:
            if card.card_id == cid:
                return card
        raise CardNotFoundError(cid)

    def update_card(self, card_id: str, front: Optional[str] = None,
                    back: Optional[str] = None) -> Card:
        card = self.get_card(card_id)
        if front is not None:
            if not str(front).strip():
                raise ValueError("front must be non-empty")
            card.front = str(front).strip()
        if back is not None:
            if not str(back).strip():
                raise ValueError("back must be non-empty")
            card.back = str(back).strip()
        return card

    def delete_card(self, card_id: str) -> Card:
        card = self.get_card(card_id)
        self.cards.remove(card)
        return card


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def backup_deck_if_exists(deck_path: Path, backups_dir: Path = None,
                          keep_backups: int = MAX_BACKUPS) -> Optional[Path]:
    """Copy an existing deck file to backups before it gets overwritten."""
    source = Path(deck_path)
    if not source.exists():
        return None
    if backups_dir is None:
        backups_dir = Path(source.parent).parent / "backups"
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_stamp()
    target = backups_dir / f"{source.stem}.{stamp}.bak.json"
    shutil.copy2(source, target)
    prune_backups(backups_dir, source.stem, keep_backups)
    return target


def prune_backups(backups_dir: Path, stem: str, keep: int) -> None:
    backups = sorted(Path(backups_dir).glob(f"{stem}.*.bak.json"))
    for old in backups[:-keep] if keep > 0 else backups:
        old.unlink()


def list_backups(deck_name: str, backups_dir: Optional[Path] = None) -> List[Path]:
    name = validate_deck_name(deck_name)
    if backups_dir is None:
        backups_dir = BACKUPS_DIR
    return sorted(Path(backups_dir).glob(f"{name}.*.bak.json"))


# ---------------------------------------------------------------------------
# Listing / search
# ---------------------------------------------------------------------------

def list_deck_files(decks_dir: Path = None) -> List[Path]:
    decks_dir = Path(DECKS_DIR if decks_dir is None else decks_dir)
    if not decks_dir.exists():
        return []
    return sorted(p for p in decks_dir.glob("*" + DECK_FILE_EXT) if p.is_file())


def search_cards(deck: Deck, query: str) -> List[Card]:
    q = (query or "").lower().strip()
    if not q:
        return list(deck.cards)
    return [c for c in deck.cards
            if q in c.front.lower() or q in c.back.lower()]


# ---------------------------------------------------------------------------
# Quiz (shuffled draw + answer reveal)
# ---------------------------------------------------------------------------

class QuizSession:
    """A shuffled draw held in memory so the agent never sees answers early.

    Order is decided by sorting cards on a Fisher-Yates-free keyed shuffle:
    each card gets a random key from a seeded RNG, then cards sort by key.
    Same seed + same deck contents => same order (deterministic, testable).
    """

    def __init__(self, deck_name: str, cards: List[Tuple[str, str, str]],
                 seed: Optional[str] = None, count: Optional[int] = None):
        """cards: list of (card_id, front, back)."""
        if seed is not None and not isinstance(seed, str):
            raise ValueError("seed must be a string or None")
        self.deck_name = deck_name
        self.seed = seed if seed else random_seed()
        self.order: List[str] = []
        self._by_id: Dict[str, Tuple[str, str]] = {cid: (front, back)
                                                   for cid, front, back in cards}
        rng = random.Random(self.seed)
        keyed = [(rng.random(), cid) for (cid, _f, _b) in cards]
        keyed.sort(key=lambda kc: kc[0])
        self.order = [cid for _key, cid in keyed]
        if count is not None and count is not False:
            if not isinstance(count, int) or count <= 0:
                raise ValueError("count must be a positive integer or None")
            self.order = self.order[:count]
        self.revealed: Dict[int, str] = {}

    def size(self) -> int:
        return len(self.order)

    def questions(self) -> List[Dict[str, Any]]:
        return [{"index": i, "card_id": cid, "front": self._by_id[cid][0]}
                for i, cid in enumerate(self.order)]

    def reveal(self, index: int) -> str:
        if not isinstance(index, int) or isinstance(index, bool):
            raise CardNotFoundError(index if isinstance(index, int) else str(index))
        if index < 0 or index >= len(self.order):
            raise CardNotFoundError(str(index))
        self.revealed[index] = self._by_id[self.order[index]][1]
        return self.revealed[index]

    def summary(self, session_id: str) -> Dict[str, Any]:
        return {
            "session": session_id,
            "deck": self.deck_name,
            "seed": self.seed,
            "total_questions": self.size(),
            "questions": self.questions(),
            "hints": "Call reveal_answer(session_id, index) to see each answer.",
        }


def random_seed() -> str:
    rng = random.SystemRandom()
    return "".join(rng.choice(SEED_CHARS) for _ in range(6))
