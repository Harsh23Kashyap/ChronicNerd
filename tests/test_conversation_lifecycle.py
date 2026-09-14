import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = (ROOT / "dietnerd-backend" / "main.py").read_text()
FRONT = (ROOT / "dietnerd-website" / "index.js").read_text()


class ConversationLifecycleContractTest(unittest.TestCase):
    def test_request_and_conversation_ids_are_distinct(self):
        self.assertIn('"request_id": request_id, "conversation_id": conversation_id', MAIN)
        self.assertIn('/sse?request_id=${requestId}', FRONT)
        self.assertNotIn('/sse?session_id=', FRONT)

    def test_query_model_accepts_durable_conversation(self):
        self.assertRegex(MAIN, r'(?s)class QueryModel\(BaseModel\):.*?conversation_id: Optional\[str\] = None')

    def test_history_is_scoped_to_conversation(self):
        self.assertIn('WHERE email = %s AND conversation_id = %s ORDER BY query_number', MAIN)
        self.assertIn('UNIQUE KEY uq_conversation_query (conversation_id, query_number)', MAIN)

    def test_query_number_allocation_is_serialized(self):
        store = (ROOT / 'dietnerd-backend' / 'conversation_store.py').read_text()
        self.assertIn('SELECT next_query_number FROM conversations', store)
        self.assertIn('FOR UPDATE', store)
        self.assertIn('next_query_number = next_query_number + 1', store)

    def test_frontend_no_longer_calls_wasted_memory_preflight(self):
        self.assertNotIn('/check_session_memory', FRONT)
        self.assertNotIn('session_memory: []', FRONT)

    def test_standalone_rewrite_has_one_backend_call_site(self):
        self.assertEqual(MAIN.count('generate_standalone_question('), 1)

    def test_frontend_can_start_select_and_delete_conversations(self):
        self.assertIn("getElementById('conversation-select')", FRONT)
        self.assertIn("getElementById('new-conversation')", FRONT)
        self.assertIn("getElementById('delete-conversation')", FRONT)

    def test_fresh_schema_creates_users_before_children(self):
        users = MAIN.index('CREATE TABLE IF NOT EXISTS users')
        conversations = MAIN.index('CREATE TABLE IF NOT EXISTS conversations')
        memory = MAIN.index('CREATE TABLE IF NOT EXISTS user_session_memory')
        self.assertLess(users, conversations)
        self.assertLess(conversations, memory)

    def test_cache_endpoint_persists_an_owned_turn(self):
        self.assertNotIn('@app.get("/db_get/', MAIN)
        cached = MAIN[MAIN.index('async def cached_answer'):MAIN.index('@app.get("/check_valid')]
        self.assertIn('conversation_belongs_to(', cached)
        self.assertIn('append_session_memory(', cached)

    def test_sse_final_payload_uses_the_frontend_envelope(self):
        self.assertIn('json.dumps({"update": data})', MAIN)
        self.assertNotIn('"final_output" in data', MAIN)

    def test_clearing_memory_resets_query_number(self):
        reset = MAIN[MAIN.index('async def reset_session_memory'):MAIN.index('@app.post("/upload_attachment")')]
        self.assertIn('SET next_query_number = 1', reset)


if __name__ == '__main__':
    unittest.main()
