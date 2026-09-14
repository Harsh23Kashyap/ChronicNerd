import json
import os
import pathlib
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "dietnerd-backend"
sys.path.insert(0, str(BACKEND))
import main


class ApiIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.create_tables()
        cls.client = TestClient(main.app)
        cls.email = "api-integration@example.com"
        connection = main._get_db_connection()
        cursor = connection.cursor()
        cursor.execute("DELETE FROM users WHERE email = %s", (cls.email,))
        cursor.execute("INSERT INTO users (email, password) VALUES (%s, %s)", (cls.email, "test-hash"))
        cursor.execute(
            "INSERT INTO question_answer (question, answer) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE answer = VALUES(answer)",
            ("cached question", json.dumps({"end_output": "cached answer", "citations": [], "citations_obj": []})),
        )
        connection.commit()
        connection.close()

    @classmethod
    def tearDownClass(cls):
        connection = main._get_db_connection()
        cursor = connection.cursor()
        cursor.execute("DELETE FROM users WHERE email = %s", (cls.email,))
        cursor.execute("DELETE FROM question_answer WHERE question = %s", ("cached question",))
        connection.commit()
        connection.close()

    def test_conversation_cache_and_isolation_flow(self):
        first = self.client.post("/conversations", params={"email": self.email})
        self.assertEqual(first.status_code, 200)
        conversation_a = first.json()["conversation_id"]
        second = self.client.post("/conversations", params={"email": self.email})
        conversation_b = second.json()["conversation_id"]
        self.assertNotEqual(conversation_a, conversation_b)

        with patch.object(main, "update_conversation_summary", return_value="updated summary"):
            cached = self.client.post("/cached_answer", json={
                "user_query": "cached question",
                "email": self.email,
                "conversation_id": conversation_a,
            })
        self.assertEqual(cached.status_code, 200)
        body = cached.json()
        self.assertEqual(body["conversation_id"], conversation_a)
        self.assertNotEqual(body["request_id"], conversation_a)

        memory_a = self.client.get("/session_memory", params={"email": self.email, "conversation_id": conversation_a})
        memory_b = self.client.get("/session_memory", params={"email": self.email, "conversation_id": conversation_b})
        self.assertEqual(memory_a.json()["count"], 1)
        self.assertEqual(memory_a.json()["entries"][0]["query_number"], 1)
        self.assertEqual(memory_a.json()["entries"][0]["answer"], "cached answer")
        self.assertEqual(memory_b.json()["count"], 0)

        listed = self.client.get("/conversations", params={"email": self.email}).json()["conversations"]
        self.assertEqual({item["conversation_id"] for item in listed}, {conversation_a, conversation_b})

        deleted = self.client.delete(f"/conversations/{conversation_b}", params={"email": self.email})
        self.assertEqual(deleted.status_code, 200)
        missing = self.client.get("/session_memory", params={"email": self.email, "conversation_id": conversation_b})
        self.assertEqual(missing.status_code, 404)

    def test_process_query_returns_two_ids_and_streams_final_answer(self):
        def fake_process(user_query, request_id, email, conversation_id):
            entry = {
                "request_id": request_id,
                "raw_question": user_query,
                "standalone_question": user_query,
                "answer": "generated answer",
            }
            main.append_session_memory(email, conversation_id, entry)
            main.loop.run_until_complete(main.send_update(request_id, {
                "end_output": "generated answer",
                "relevant_articles": [],
                "citations_obj": [],
                "citations": [],
                "session_memory_entry": entry,
            }))

        with patch.object(main, "process_user_query", side_effect=fake_process):
            started = self.client.post("/process_query", json={"user_query": "fresh question", "email": self.email})
        self.assertEqual(started.status_code, 200)
        identifiers = started.json()
        self.assertNotEqual(identifiers["request_id"], identifiers["conversation_id"])

        with self.client.stream("GET", "/sse", params={"request_id": identifiers["request_id"]}) as stream:
            text = "".join(stream.iter_text())
        self.assertIn('"end_output": "generated answer"', text)

        memory = self.client.get("/session_memory", params={
            "email": self.email,
            "conversation_id": identifiers["conversation_id"],
        }).json()
        self.assertEqual(memory["count"], 1)
        self.assertEqual(memory["entries"][0]["answer"], "generated answer")


if __name__ == "__main__":
    unittest.main()
