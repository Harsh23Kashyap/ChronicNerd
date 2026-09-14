import concurrent.futures
import os
import pathlib
import sys
import unittest
import uuid

import mysql.connector

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "dietnerd-backend"))
from conversation_store import append_turn


@unittest.skipUnless(os.getenv("RUN_MYSQL_INTEGRATION") == "1", "requires local MySQL fixture")
class MySqlConcurrencyTest(unittest.TestCase):
    def connection(self):
        return mysql.connector.connect(
            host=os.environ["host"],
            port=int(os.environ["port"]),
            user=os.environ["user"],
            password=os.environ.get("password", ""),
            database=os.environ["database"],
        )

    def test_concurrent_turns_are_numbered_without_collisions(self):
        email = "concurrency@example.com"
        conversation_id = str(uuid.uuid4())
        connection = self.connection()
        cursor = connection.cursor()
        cursor.execute("DELETE FROM users WHERE email = %s", (email,))
        cursor.execute("INSERT INTO users (email, password) VALUES (%s, %s)", (email, "test"))
        cursor.execute(
            "INSERT INTO conversations (conversation_id, email, title) VALUES (%s, %s, %s)",
            (conversation_id, email, "Concurrency test"),
        )
        connection.commit()
        connection.close()

        def add(index):
            return append_turn(self.connection, email, conversation_id, {
                "request_id": str(uuid.uuid4()),
                "raw_question": f"q{index}",
                "standalone_question": f"q{index}",
                "answer": f"a{index}",
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            numbers = list(pool.map(add, range(20)))
        self.assertEqual(sorted(numbers), list(range(1, 21)))

        connection = self.connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT query_number FROM user_session_memory WHERE conversation_id = %s ORDER BY query_number",
            (conversation_id,),
        )
        self.assertEqual([row[0] for row in cursor.fetchall()], list(range(1, 21)))
        cursor.execute("DELETE FROM users WHERE email = %s", (email,))
        connection.commit()
        connection.close()
