#!/usr/bin/env bash
set -euo pipefail
: "${MYSQL_SOCKET:?Set MYSQL_SOCKET to the local MySQL fixture socket}"
MYSQL=(mysql --protocol=socket --socket="$MYSQL_SOCKET" -uroot)
DB=chronicnerd_migration_test
"${MYSQL[@]}" <<SQL
DROP DATABASE IF EXISTS $DB;
CREATE DATABASE $DB;
USE $DB;
CREATE TABLE users (email VARCHAR(255) PRIMARY KEY, password VARCHAR(255) NOT NULL);
INSERT INTO users VALUES ('alice@example.com','legacy-hash'),('bob@example.com','legacy-hash');
CREATE TABLE user_session_memory (id INT AUTO_INCREMENT PRIMARY KEY,email VARCHAR(255) NOT NULL,session_id VARCHAR(255),raw_question TEXT,standalone_question TEXT,answer LONGTEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE);
CREATE TABLE user_conversation_summary (email VARCHAR(255) PRIMARY KEY,summary LONGTEXT NOT NULL,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE);
INSERT INTO user_session_memory(email,session_id,raw_question,standalone_question,answer) VALUES ('alice@example.com','r1','q1','q1','a1'),('alice@example.com','r2','q2','q2','a2'),('bob@example.com','r3','q3','q3','a3');
INSERT INTO user_conversation_summary(email,summary) VALUES ('alice@example.com','alice summary'),('bob@example.com','bob summary');
SQL
"${MYSQL[@]}" "$DB" < dietnerd-backend/migrations/001_conversation_lifecycle.sql
read -r conversations turns summaries legacy <<<"$("${MYSQL[@]}" -N -B "$DB" -e 'SELECT (SELECT COUNT(*) FROM conversations),(SELECT COUNT(*) FROM user_session_memory),(SELECT COUNT(*) FROM user_conversation_summary),(SELECT COUNT(*) FROM user_conversation_summary_legacy)')"
[[ "$conversations $turns $summaries $legacy" == "2 3 2 2" ]]
sequence=$("${MYSQL[@]}" -N -B "$DB" -e "SELECT GROUP_CONCAT(query_number ORDER BY query_number) FROM user_session_memory WHERE email='alice@example.com'")
[[ "$sequence" == "1,2" ]]
"${MYSQL[@]}" -e "DROP DATABASE $DB"
echo "migration fixture passed"
