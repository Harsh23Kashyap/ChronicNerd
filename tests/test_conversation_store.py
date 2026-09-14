import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "dietnerd-backend"))
from conversation_store import append_turn


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._row = None

    def execute(self, sql, params):
        self.connection.calls.append((sql, params))
        if sql.startswith("SELECT next_query_number"):
            conversation_id, email = params
            record = self.connection.conversations.get((email, conversation_id))
            self._row = (record["next"],) if record else None
        elif sql.startswith("UPDATE conversations"):
            conversation_id, email = params
            self.connection.conversations[(email, conversation_id)]["next"] += 1
        elif sql.startswith("INSERT INTO user_session_memory"):
            self.connection.turns.append(params)

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self):
        self.conversations = {
            ("a@example.com", "conversation-a"): {"next": 1},
            ("a@example.com", "conversation-b"): {"next": 1},
        }
        self.turns = []
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def start_transaction(self):
        self.calls.append(("START", ()))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class ConversationStoreTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeConnection()
        self.factory = lambda: self.db
        self.entry = {
            "request_id": "request-1",
            "raw_question": "raw",
            "standalone_question": "standalone",
            "answer": "answer",
        }

    def test_numbering_is_per_conversation(self):
        self.assertEqual(append_turn(self.factory, "a@example.com", "conversation-a", self.entry), 1)
        self.assertEqual(append_turn(self.factory, "a@example.com", "conversation-a", self.entry), 2)
        self.assertEqual(append_turn(self.factory, "a@example.com", "conversation-b", self.entry), 1)
        self.assertEqual([turn[2] for turn in self.db.turns], [1, 2, 1])
        self.assertEqual(self.db.commits, 3)

    def test_owner_is_part_of_locked_lookup(self):
        with self.assertRaises(ValueError):
            append_turn(self.factory, "other@example.com", "conversation-a", self.entry)
        select = next(sql_params for sql_params in self.db.calls if sql_params[0].startswith("SELECT"))
        self.assertEqual(select[1], ("conversation-a", "other@example.com"))
        self.assertEqual(self.db.rollbacks, 1)
        self.assertEqual(self.db.turns, [])


if __name__ == "__main__":
    unittest.main()
