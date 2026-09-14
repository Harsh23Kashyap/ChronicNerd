-- Upgrade the original email-scoped memory schema to durable conversations.
-- Back up the database before applying. Run exactly once on MySQL 8.x.
-- Fresh databases do not run this file; main.py creates the final schema.

CREATE TABLE IF NOT EXISTS users (
    email VARCHAR(255) PRIMARY KEY,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id VARCHAR(36) PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    title VARCHAR(255),
    next_query_number INT NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_conversations_email (email),
    CONSTRAINT fk_conversations_user FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE
);

-- Every user's old email-scoped history becomes one clearly titled legacy
-- conversation. UUID() is evaluated once per grouped row.
INSERT INTO conversations (conversation_id, email, title, next_query_number, created_at, updated_at)
SELECT UUID(), memory.email, 'Imported legacy history', COUNT(*) + 1,
       MIN(memory.created_at), MAX(memory.created_at)
FROM user_session_memory AS memory
LEFT JOIN conversations AS existing
  ON existing.email = memory.email AND existing.title = 'Imported legacy history'
WHERE existing.conversation_id IS NULL
GROUP BY memory.email;

ALTER TABLE user_session_memory
    ADD COLUMN conversation_id VARCHAR(36) NULL AFTER email,
    ADD COLUMN query_number INT NULL AFTER conversation_id,
    CHANGE COLUMN session_id request_id VARCHAR(36) NULL;

UPDATE user_session_memory AS memory
JOIN conversations AS conversation
  ON conversation.email = memory.email
 AND conversation.title = 'Imported legacy history'
SET memory.conversation_id = conversation.conversation_id
WHERE memory.conversation_id IS NULL;

UPDATE user_session_memory AS memory
JOIN (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY email ORDER BY id) AS turn_number
    FROM user_session_memory
) AS numbered ON numbered.id = memory.id
SET memory.query_number = numbered.turn_number
WHERE memory.query_number IS NULL;

ALTER TABLE user_session_memory
    MODIFY conversation_id VARCHAR(36) NOT NULL,
    MODIFY query_number INT NOT NULL,
    ADD UNIQUE KEY uq_conversation_query (conversation_id, query_number),
    ADD CONSTRAINT fk_memory_conversation
      FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE;

CREATE TABLE user_conversation_summary_v2 (
    email VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(36) NOT NULL,
    summary LONGTEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (email, conversation_id),
    CONSTRAINT fk_summary_user FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE,
    CONSTRAINT fk_summary_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
);

INSERT INTO user_conversation_summary_v2 (email, conversation_id, summary, updated_at)
SELECT summary.email, conversation.conversation_id, summary.summary, summary.updated_at
FROM user_conversation_summary AS summary
JOIN conversations AS conversation
  ON conversation.email = summary.email
 AND conversation.title = 'Imported legacy history';

RENAME TABLE user_conversation_summary TO user_conversation_summary_legacy,
             user_conversation_summary_v2 TO user_conversation_summary;

-- Verify the migrated counts and summaries before dropping the backup table:
-- DROP TABLE user_conversation_summary_legacy;
