"""Unit tests for the flashcards MCP server (standard library unittest).

Covers:
  - JSON storage round-trips (save/load, create/load/update/delete)
  - auto-backup creation and pruning
  - shuffle determinism (same seed -> same order; new draw differs)
  - quiz draw / answer-reveal (answers hidden, reveal works, bad index fails)
  - path safety (traversal, separators, escapes) and deck name validation
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import flashcards_core as core
import flashcards_server as server


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="flashcards-test-"))
        self.decks = self.tmp / "decks"
        self.backups = self.tmp / "backups"
        self.decks.mkdir(parents=True)
        self.backups.mkdir(parents=True)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp, topdown=False):
            for name in files:
                os.remove(Path(root) / name)
            os.rmdir(root)

    def make_deck(self, name: str = "Test Deck", n: int = 5) -> core.Deck:
        deck = core.Deck(name)
        for i in range(n):
            deck.add_card(f"front {i}", f"back {i}")
        deck.save(self.decks)
        return deck


class DeckRoundTripTests(BaseTestCase):
    def test_save_then_load_roundtrip(self):
        deck = self.make_deck("Round Trip", n=3)
        loaded = core.Deck.load("Round Trip", self.decks)
        self.assertEqual(loaded.to_dict(), deck.to_dict())
        self.assertEqual([c.front for c in loaded.cards],
                         ["front 0", "front 1", "front 2"])

    def test_card_ids_preserved(self):
        deck = self.make_deck("Id Check", n=2)
        ids = [c.card_id for c in deck.cards]
        loaded = core.Deck.load("Id Check", self.decks)
        self.assertEqual([c.card_id for c in loaded.cards], ids)

    def test_load_missing_deck_raises(self):
        with self.assertRaises(core.DeckNotFoundError):
            core.Deck.load("Never Existed", self.decks)

    def test_add_card_requires_both_sides(self):
        deck = core.Deck("Empty Check")
        with self.assertRaises(ValueError):
            deck.add_card("front only", "")
        self.assertEqual(len(deck.cards), 0)

    def test_update_card_roundtrip(self):
        self.make_deck("Update Me", n=1)
        deck = core.Deck.load("Update Me", self.decks)
        card_id = deck.cards[0].card_id
        deck.update_card(card_id, back="new back")
        deck.save(self.decks)
        loaded = core.Deck.load("Update Me", self.decks)
        self.assertEqual(loaded.get_card(card_id).back, "new back")

    def test_delete_card_roundtrip(self):
        self.make_deck("Delete Me", n=3)
        deck = core.Deck.load("Delete Me", self.decks)
        victim = deck.cards[1].card_id
        deck.delete_card(victim)
        deck.save(self.decks)
        loaded = core.Deck.load("Delete Me", self.decks)
        self.assertEqual(len(loaded.cards), 2)
        with self.assertRaises(core.CardNotFoundError):
            loaded.get_card(victim)

    def test_deck_file_is_valid_json_on_disk(self):
        self.make_deck("Json Check", n=1)
        with open(self.decks / "Json Check.json", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["name"], "Json Check")
        self.assertEqual(len(data["cards"]), 1)

    def test_save_writes_atomically_no_tmp_leftovers(self):
        self.make_deck("Atomic", n=1)
        leftovers = [p.name for p in self.decks.iterdir()
                     if p.name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])


class BackupTests(BaseTestCase):
    def test_backup_created_on_overwrite(self):
        self.make_deck("Backed Up", n=1)
        deck = core.Deck.load("Backed Up", self.decks)
        deck.add_card("extra front", "extra back")
        deck.save(self.decks)
        backups = core.list_backups("Backed Up", self.backups)
        self.assertEqual(len(backups), 1)
        with open(backups[0], encoding="utf-8") as fh:
            old = json.load(fh)
        self.assertEqual(len(old["cards"]), 1)

    def test_backups_pruned_to_max(self):
        self.make_deck("Pruned", n=1)
        deck = core.Deck.load("Pruned", self.decks)
        for i in range(8):
            deck.add_card(f"f{i}", f"b{i}")
            deck.save(self.decks, keep_backups=core.MAX_BACKUPS)
        backups = core.list_backups("Pruned", self.backups)
        self.assertEqual(len(backups), core.MAX_BACKUPS)

    def test_no_backup_on_first_save(self):
        deck = core.Deck("Fresh Deck")
        deck.add_card("a", "b")
        deck.save(self.decks)
        self.assertEqual(core.list_backups("Fresh Deck", self.backups), [])

    def test_backup_directory_confined(self):
        self.make_deck("Backup Scope", n=1)
        deck = core.Deck.load("Backup Scope", self.decks)
        deck.add_card("x", "y")
        deck.save(self.decks, keep_backups=3)
        self.assertTrue(self.backups.exists())
        created = list(self.backups.iterdir())
        for path in created:
            self.assertTrue(path.is_relative_to(self.backups),
                            f"backup escaped backups dir: {path}")


class PathSafetyTests(BaseTestCase):
    def test_reject_dotdot(self):
        with self.assertRaises(core.DeckNameError):
            core.resolve_deck_path("../evil", self.decks)

    def test_reject_nested_traversal(self):
        with self.assertRaises(core.DeckNameError):
            core.resolve_deck_path("ok/../../evil", self.decks)

    def test_reject_slash(self):
        with self.assertRaises(core.DeckNameError):
            core.resolve_deck_path("sub/deck", self.decks)

    def test_reject_backslash(self):
        with self.assertRaises(core.DeckNameError):
            core.resolve_deck_path("sub\\deck", self.decks)

    def test_reject_colon(self):
        with self.assertRaises(core.DeckNameError):
            core.resolve_deck_path("C:evil", self.decks)

    def test_reject_leading_dot(self):
        with self.assertRaises(core.DeckNameError):
            core.resolve_deck_path(".hidden", self.decks)

    def test_reject_dot_names(self):
        for name in (".", ".."):
            with self.assertRaises(core.DeckNameError):
                core.resolve_deck_path(name, self.decks)

    def test_reject_empty(self):
        with self.assertRaises(core.DeckNameError):
            core.resolve_deck_path("   ", self.decks)

    def test_reject_too_long(self):
        with self.assertRaises(core.DeckNameError):
            core.resolve_deck_path("x" * 65, self.decks)

    def test_reject_special_chars(self):
        for name in ("bad*name", "bad?name", "bad$name", "bad%name", "bad\tname"):
            with self.assertRaises(core.DeckNameError):
                core.resolve_deck_path(name, self.decks)

    def test_resolved_path_stays_inside_decks_dir(self):
        for name in ("Study Deck", "Under_Score-Name 123"):
            path = core.resolve_deck_path(name, self.decks)
            self.assertTrue(path.is_relative_to(self.decks.resolve()))
            self.assertEqual(path.suffix, ".json")

    def test_save_load_traversal_name_never_touches_disk(self):
        # constructing a deck under a traversal name is impossible
        with self.assertRaises(core.DeckNameError):
            core.Deck("../evil-canary")

        with self.assertRaises(core.DeckNotFoundError):
            core.Deck.load("evil", self.decks)

        parent = self.decks.parent
        canaries = [p.name for p in parent.iterdir()
                    if "evil" in p.name.lower()]
        self.assertEqual(canaries, [])

    def test_load_of_corrupt_deck_file_raises_valueerror(self):
        self.decks.joinpath("Corrupt.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            core.Deck.load("Corrupt", self.decks)


class ShuffleDeterminismTests(BaseTestCase):
    def cards_for(self, deck: core.Deck):
        return [(c.card_id, c.front, c.back) for c in deck.cards]

    def test_same_seed_same_order(self):
        deck = self.make_deck("Det Deck", n=20)
        cards = self.cards_for(deck)
        s1 = core.QuizSession("Det Deck", cards, seed="FIXED1", count=None)
        s2 = core.QuizSession("Det Deck", cards, seed="FIXED1", count=None)
        self.assertEqual(s1.order, s2.order)
        self.assertEqual(s1.size(), 20)

    def test_same_seed_same_questions_and_answers(self):
        deck = self.make_deck("Det Pair", n=8)
        cards = self.cards_for(deck)
        s1 = core.QuizSession("Det Pair", cards, seed="ABC123")
        s2 = core.QuizSession("Det Pair", cards, seed="ABC123")
        for i in range(s1.size()):
            self.assertEqual(s1.questions()[i], s2.questions()[i])
            self.assertEqual(s1.reveal(i), s2.reveal(i))

    def test_different_usually_different(self):
        seed_sets = set()
        deck = self.make_deck("Var Deck", n=12)
        cards = self.cards_for(deck)
        for seed in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"):
            q = core.QuizSession("Var Deck", cards, seed=seed)
            seed_sets.add(tuple(q.order))
        self.assertGreater(len(seed_sets), 1, "8 different seeds produced "
                                             "identical orders")

    def test_random_seed_is_drawn_without_seed(self):
        deck = self.make_deck("Auto Seed", n=6)
        cards = self.cards_for(deck)
        q = core.QuizSession("Auto Seed", cards, seed=None)
        self.assertEqual(len(q.seed), 6)
        self.assertTrue(all(ch in core.SEED_CHARS for ch in q.seed))

    def test_draw_n_returns_n_with_answers_hidden(self):
        deck = self.make_deck("Draw N", n=10)
        cards = self.cards_for(deck)
        q = core.QuizSession("Draw N", cards, seed="SEEDQ", count=4)
        self.assertEqual(q.size(), 4)
        for question in q.questions():
            self.assertNotIn("back", question)
            self.assertEqual(question["front"], deck.get_card(question["card_id"]).front)

    def test_draw_n_is_prefix_of_full_session(self):
        deck = self.make_deck("Prefix Deck", n=10)
        cards = self.cards_for(deck)
        full = core.QuizSession("Prefix Deck", cards, seed="SAME")
        part = core.QuizSession("Prefix Deck", cards, seed="SAME", count=3)
        self.assertEqual(part.order, full.order[:3])

    def test_shuffle_positions_change_for_most_cards(self):
        deck = self.make_deck("Shuffle Move", n=20)
        cards = self.cards_for(deck)
        before = [c.card_id for c in deck.cards]
        shuffled = core.QuizSession("Shuffle Move", cards, seed="SHUF1").order
        moved = sum(1 for a, b in zip(before, shuffled) if a != b)
        self.assertGreaterEqual(moved, 12, "shuffle barely moved cards")

    def test_count_must_be_valid(self):
        deck = self.make_deck("Count Deck", n=3)
        cards = self.cards_for(deck)
        self.assertEqual(core.QuizSession("Count Deck", cards, count=None).size(), 3)
        with self.assertRaises(ValueError):
            core.QuizSession("Count Deck", cards, count=0)
        with self.assertRaises(ValueError):
            core.QuizSession("Count Deck", cards, count=-2)


class QuizRevealTests(BaseTestCase):
    def test_reveal_returns_back_text(self):
        deck = self.make_deck("Reveal Deck", n=5)
        q = core.QuizSession("R", [(c.card_id, c.front, c.back)
                                   for c in deck.cards], seed="RRR")
        questions = q.questions()
        first_front = questions[0]["front"]
        card = next(c for c in deck.cards if c.front == first_front)
        self.assertEqual(q.reveal(0), card.back)

    def test_reveal_tracks_revealed(self):
        deck = self.make_deck("Track", n=3)
        q = core.QuizSession("T", [(c.card_id, c.front, c.back)
                                   for c in deck.cards])
        q.reveal(0)
        q.reveal(2)
        self.assertEqual(sorted(q.revealed), [0, 2])
        summary = q.summary("sess-1")
        self.assertEqual(summary["total_questions"], 3)

    def test_reveal_invalid_index_raises(self):
        deck = self.make_deck("Bad Index", n=2)
        q = core.QuizSession("B", [(c.card_id, c.front, c.back)
                                   for c in deck.cards])
        with self.assertRaises(core.CardNotFoundError):
            q.reveal(2)
        with self.assertRaises(core.CardNotFoundError):
            q.reveal(-1)

    def test_reveal_does_not_mutate_card_files(self):
        self.make_deck("Immutable Check", n=2)
        before = (self.decks / "Immutable Check.json").read_text(encoding="utf-8")
        deck = core.Deck.load("Immutable Check", self.decks)
        q = core.QuizSession("I", [(c.card_id, c.front, c.back)
                                   for c in deck.cards])
        q.reveal(0)
        after = (self.decks / "Immutable Check.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)


class ServerToolTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.db = server.Database(self.decks, self.backups)
        server.DB = self.db

    def build_request(self, tool: str, args: dict):
        params = {"name": tool, "arguments": args}
        response = server._dispatch_tool_call(params,
                                              lambda result: {"ok": result},
                                              lambda code, msg: {"err": msg})
        return self.unwrap(response)

    def unwrap(self, response):
        self.assertNotIn("err", response, f"tool error: {response}")
        self.assertIn("ok", response)
        payload = response["ok"]
        self.assertFalse(payload.get("isError"), payload)
        text = payload["content"][0]["text"]
        return json.loads(text)

    def test_create_then_list_cards(self):
        out = self.build_request("create_card",
                                 {"deck_name": "Srv Deck", "front": "F",
                                  "back": "B"})
        self.assertTrue(out["created"])
        cards = self.build_request("list_cards", {"deck_name": "Srv Deck"})
        self.assertEqual(len(cards["cards"]), 1)
        self.assertEqual(cards["cards"][0]["front"], "F")
        self.assertEqual(cards["cards"][0]["back"], "B")

    def test_create_card_auto_creates_missing_deck(self):
        self.assertFalse((self.decks / "Brand New.json").exists())
        self.build_request("create_card",
                           {"deck_name": "Brand New", "front": "F1", "back": "B1"})
        self.assertTrue((self.decks / "Brand New.json").exists())

    def test_search_cards_finds_matches(self):
        self.build_request("create_card",
                           {"deck_name": "Search", "front": "What is MIT?",
                            "back": "A permissive license"})
        self.build_request("create_card",
                           {"deck_name": "Search", "front": "What is GPL?",
                            "back": "Copyleft"})
        out = self.build_request("search_cards",
                                 {"deck_name": "Search", "query": "license"})
        self.assertEqual(out["match_count"], 1)
        self.assertEqual(out["cards"][0]["back"], "A permissive license")

    def test_update_and_delete_card(self):
        created = self.build_request("create_card",
                                     {"deck_name": "Mutate", "front": "F0",
                                      "back": "B0"})
        card_id = created["card"]["id"]
        updated = self.build_request("update_card",
                                     {"deck_name": "Mutate", "card_id": card_id,
                                      "back": "B1"})
        self.assertEqual(updated["card"]["back"], "B1")
        deleted = self.build_request("delete_card",
                                     {"deck_name": "Mutate", "card_id": card_id})
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["card_count"], 0)

    def test_draw_quiz_hides_answers_and_reveal_shows(self):
        d = core.Deck("Quiz Srv")
        for i in range(4):
            d.add_card(f"f{i}", f"b{i}")
        d.save(self.decks)
        draw = self.build_request("draw_quiz",
                                  {"deck_name": "Quiz Srv", "count": 3,
                                   "seed": "SRVQ"})
        self.assertEqual(draw["total_questions"], 3)
        for question in draw["questions"]:
            self.assertNotIn("back", question)
            self.assertNotIn("answer", question)
        reveal = self.build_request("reveal_answer",
                                    {"session_id": draw["session"],
                                     "index": 0})
        front0 = draw["questions"][0]["front"]
        expected = next(c for c in d.cards if c.front == front0).back
        self.assertEqual(reveal["answer"], expected)

    def test_reveal_without_session_errors(self):
        result = server._dispatch_tool_call(
            {"name": "reveal_answer",
             "arguments": {"session_id": "ghost", "index": 0}},
            lambda r: r, lambda code, msg: {"err": msg})
        self.assertIn("KeyError", json.dumps(result))

    def test_traversal_deck_name_is_tool_safe(self):
        result = server._dispatch_tool_call(
            {"name": "create_card",
             "arguments": {"deck_name": "../escape", "front": "f", "back": "b"}},
            lambda r: r, lambda code, msg: {"err": msg})
        self.assertIn("DeckNameError", json.dumps(result))
        canaries = [p.name for p in self.decks.parent.iterdir()
                    if "escape" in p.name.lower()]
        self.assertEqual(canaries, [])

    def test_list_decks_shows_files(self):
        self.make_deck("Visible Deck", n=1)
        out = self.build_request("list_decks", {})
        names = [d["deck"] for d in out["decks"]]
        self.assertIn("Visible Deck", names)

    def test_unknown_tool_rejected(self):
        result = server._dispatch_tool_call(
            {"name": "not_a_tool", "arguments": {}},
            lambda r: r, lambda code, msg: {"err": msg})
        self.assertIn("Unknown tool", json.dumps(result))


class RpcProtocolTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.db = server.Database(self.decks, self.backups)
        server.DB = self.db

    def call(self, request):
        return server.handle_rpc(request)

    def test_initialize_handshake(self):
        response = self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18"}})
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "flashcards")

    def test_tools_list_matches_dispatch_table(self):
        response = self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in response["result"]["tools"]]
        self.assertEqual(set(names), set(server.TOOLS))
        for schema in response["result"]["tools"]:
            self.assertIn("inputSchema", schema)
            self.assertIn("description", schema)

    def test_notification_gets_no_response(self):
        self.assertIsNone(self.call({"jsonrpc": "2.0", "method":
                                     "notifications/initialized"}))

    def test_unknown_method_gives_error_code_minus_32601(self):
        response = self.call({"jsonrpc": "2.0", "id": 9, "method": "bogus/method"})
        self.assertEqual(response["error"]["code"], -32601)

    def test_tools_call_bad_arguments_return_isError(self):
        response = self.call({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                              "params": {"name": "create_card",
                                         "arguments": {"deck_name": "X"}}})
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("error", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
