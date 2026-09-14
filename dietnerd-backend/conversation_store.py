"""Database operations for durable conversation turns."""


def append_turn(connection_factory, email, conversation_id, entry):
    """Append one turn and atomically allocate its per-conversation number."""
    connection = connection_factory()
    try:
        cursor = connection.cursor()
        connection.start_transaction()
        cursor.execute(
            "SELECT next_query_number FROM conversations "
            "WHERE conversation_id = %s AND email = %s FOR UPDATE",
            (conversation_id, email),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError("Conversation not found")
        query_number = row[0]
        cursor.execute(
            "UPDATE conversations SET next_query_number = next_query_number + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE conversation_id = %s AND email = %s",
            (conversation_id, email),
        )
        cursor.execute(
            "INSERT INTO user_session_memory "
            "(email, conversation_id, query_number, request_id, raw_question, standalone_question, answer) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                email,
                conversation_id,
                query_number,
                entry.get("request_id"),
                entry.get("raw_question"),
                entry.get("standalone_question"),
                entry.get("answer"),
            ),
        )
        connection.commit()
        return query_number
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
