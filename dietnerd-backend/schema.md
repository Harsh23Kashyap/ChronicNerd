# DietNerd Database Schema

All tables live in the MySQL database configured via `ATT81274.env` (AWS RDS).
Tables are auto-created on server startup in `main.py`.

## Tables

### `users`

Authentication table. Created by the `/register` endpoint.

| Column     | Type         | Notes                          |
|------------|--------------|--------------------------------|
| `email`    | VARCHAR(255) | **Primary key**                |
| `password` | VARCHAR(255) | SHA-256 hashed                 |

### `user_session_memory`

Per-user conversation history. One row per question/answer exchange.

| Column                | Type         | Notes                                              |
|-----------------------|--------------|----------------------------------------------------|
| `id`                  | INT          | Auto-increment primary key                         |
| `email`               | VARCHAR(255) | FK → `users.email` (CASCADE delete)                |
| `session_id`          | VARCHAR(255) | UUID of the processing session                     |
| `raw_question`        | TEXT         | The question exactly as the user typed it           |
| `standalone_question` | TEXT         | Rewritten question with conversation context baked in |
| `answer`              | LONGTEXT     | The generated answer                               |
| `created_at`          | TIMESTAMP    | Defaults to current time                           |

Used by the backend to:
- Rewrite vague follow-ups into standalone questions (e.g. "what about zinc?" → "What are the benefits of zinc for sleep?")
- Provide conversation context across browser sessions (persists in DB, not sessionStorage)

### `user_conversation_summary`

Rolling summary of the user's conversation so far. One row per user, updated after each answer.

| Column       | Type         | Notes                                      |
|--------------|--------------|--------------------------------------------|
| `email`      | VARCHAR(255) | **Primary key**, FK → `users.email` (CASCADE) |
| `summary`    | LONGTEXT     | GPT-generated summary of all Q&A so far    |
| `updated_at` | TIMESTAMP    | Auto-updates on every write                |

### `user_documents`

User-uploaded files (PDF, TXT, CSV, JSON). Stored as extracted text, not raw bytes.

| Column       | Type         | Notes                                              |
|--------------|--------------|----------------------------------------------------|
| `id`         | INT          | Auto-increment primary key                         |
| `email`      | VARCHAR(255) | FK → `users.email` (CASCADE delete)                |
| `filename`   | VARCHAR(500) | Original filename                                  |
| `content`    | LONGTEXT     | Extracted text content of the file                  |
| `created_at` | TIMESTAMP    | Defaults to current time                           |

Unique constraint on `(email, filename)` — re-uploading the same filename replaces the content.

## Relationships

```
users.email ─┬─< user_session_memory.email
             ├─< user_conversation_summary.email
             └─< user_documents.email
```

All child tables cascade on delete — removing a user wipes their memory, summary, and documents.

## API → Table mapping

| Endpoint                  | Method | Table(s) touched                                     |
|---------------------------|--------|------------------------------------------------------|
| `/register`               | POST   | `users` (insert)                                     |
| `/login`                  | POST   | `users` (select)                                     |
| `/session_memory`         | GET    | `user_session_memory` + `user_conversation_summary`  |
| `/session_memory`         | DELETE | `user_session_memory` + `user_conversation_summary`  |
| `/check_session_memory`   | POST   | `user_session_memory` (select for context)           |
| `/process_query`          | POST   | `user_session_memory` (insert) + `user_conversation_summary` (upsert) + `user_documents` (select) |
| `/upload_attachment`      | POST   | `user_documents` (upsert)                            |
| `/list_attachments`       | GET    | `user_documents` (select)                            |
| `/remove_attachment`      | DELETE | `user_documents` (delete)                            |

Every endpoint that touches per-user data requires an `email` parameter (query param for GET/DELETE, body field for POST).
