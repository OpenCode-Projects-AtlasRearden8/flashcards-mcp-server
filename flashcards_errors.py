"""Shared error types for the flashcards server (kept tiny by design)."""


class DeckNameError(ValueError):
    """Deck name failed validation or would escape the decks directory."""


class DeckNotFoundError(KeyError):
    """No deck file exists for the given name."""


class CardNotFoundError(KeyError):
    """No matching card id or quiz question index."""
