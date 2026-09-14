from helper_functions import *
from conversation_store import append_turn

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

import asyncio
from sse_starlette.sse import EventSourceResponse
from concurrent.futures import ThreadPoolExecutor

import uuid
import json
from urllib.parse import unquote

from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

import heapq
import hashlib
import os
import mysql.connector

import logging

#Sim search
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from collections import defaultdict

logging.basicConfig(level=logging.INFO)

update_queues = defaultdict(asyncio.Queue)

app = FastAPI()

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def create_tables():
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email VARCHAR(255) PRIMARY KEY,
                password VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id VARCHAR(36) PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                title VARCHAR(255),
                next_query_number INT NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_conversations_email (email),
                FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_session_memory (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                conversation_id VARCHAR(36) NOT NULL,
                query_number INT NOT NULL,
                request_id VARCHAR(36),
                raw_question TEXT,
                standalone_question TEXT,
                answer LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_email (email),
                UNIQUE KEY uq_conversation_query (conversation_id, query_number),
                FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE,
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_conversation_summary (
                email VARCHAR(255) NOT NULL,
                conversation_id VARCHAR(36) NOT NULL,
                summary LONGTEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (email, conversation_id),
                FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE,
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_documents (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                filename VARCHAR(500) NOT NULL,
                content LONGTEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_email_filename (email, filename),
                INDEX idx_email (email),
                FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE
            )
        """)
        connection.commit()
        logging.info("[STARTUP] Database tables verified/created")
    finally:
        connection.close()

class QueryModel(BaseModel):
    user_query: str
    email: str
    conversation_id: Optional[str] = None

class AuthModel(BaseModel):
    email: str
    password: str

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def _get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('host'),
        port=os.getenv('port'),
        user=os.getenv('user'),
        password=os.getenv('password'),
        database=os.getenv('database')
    )

@app.post("/register")
async def register(auth: AuthModel):
    email = auth.email.strip().lower()
    password = auth.password
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT email FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="User already exists.")
        cursor.execute("INSERT INTO users (email, password) VALUES (%s, %s)", (email, _hash_password(password)))
        connection.commit()
    finally:
        connection.close()
    logging.info(f"[AUTH] Registered new user: {email}")
    return {"message": "Registration successful."}

@app.post("/login")
async def login(auth: AuthModel):
    email = auth.email.strip().lower()
    password = auth.password
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT password FROM users WHERE email = %s", (email,))
        row = cursor.fetchone()
    finally:
        connection.close()
    if not row:
        raise HTTPException(status_code=401, detail="User not found.")
    if row[0] != _hash_password(password):
        raise HTTPException(status_code=401, detail="Incorrect password.")
    logging.info(f"[AUTH] Login successful: {email}")
    return {"message": "Login successful.", "email": email}

disclaimer = """
DietNerd is an exploratory tool designed to enrich your conversations with a registered dietitian or registered dietitian nutritionist, who can then review your profile before providing recommendations.
Please be aware that the insights provided by DietNerd may not fully take into consideration all potential medication interactions or pre-existing conditions.
To find a local expert near you, use this website: https://www.eatright.org/find-a-nutrition-expert
"""

executor = ThreadPoolExecutor()

# Create a global event loop
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

def run_in_executor(func, *args):
    return loop.run_in_executor(executor, func, *args)

@app.get("/")
async def root():
    logging.info("Root route accessed")
    return "Hello! Go to /docs!'"

@app.get("/db_sim_search/{question:str}")
async def sim_search(question:str):
   decoded_query = unquote(question)
   result = await sim_score(decoded_query)
   return result

@app.post("/cached_answer")
async def cached_answer(query: QueryModel):
    conversation_id = query.conversation_id
    if conversation_id and not conversation_belongs_to(query.email, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Cache lookup uses the literal question. Context rewriting belongs only to
    # the generation path, so a cache miss cannot invoke the rewrite model twice.
    standalone_question = query.user_query
    result = await query_db_final(standalone_question)
    if not result:
        raise HTTPException(status_code=404, detail="Cached answer not found.")

    if not conversation_id:
        conversation_id = create_conversation(query.email, query.user_query[:120])
    request_id = str(uuid.uuid4())
    cached_payload = result[0][1]
    try:
        cached_answer_text = json.loads(cached_payload)["end_output"]
    except (TypeError, ValueError, KeyError, IndexError):
        raise HTTPException(status_code=500, detail="Cached answer has an invalid format.")
    append_session_memory(query.email, conversation_id, {
        "request_id": request_id,
        "raw_question": query.user_query,
        "standalone_question": standalone_question,
        "answer": cached_answer_text,
    })
    summary = update_conversation_summary(
        get_conversation_summary(query.email, conversation_id),
        standalone_question,
        cached_answer_text,
    )
    set_conversation_summary(query.email, conversation_id, summary)
    return {
        "cached_payload": cached_payload,
        "conversation_id": conversation_id,
        "request_id": request_id,
    }

@app.get("/check_valid/{question:str}")
async def check_valid(question:str):
   question_validity = determine_question_validity(question)
   if question_validity == 'False - Meal Plan/Recipe':
    final_output = ("I'm sorry, I cannot help you with this question. For any questions or advice around meal planning or recipes, please speak to a registered dietitian or registered dietitian nutritionist.\n"
                    "To find a local expert near you, use this website: https://www.eatright.org/find-a-nutrition-expert.")
    print(final_output)
   elif question_validity == 'False - Animal':
    final_output = ("I'm sorry, I cannot help you with this question. For any questions regarding an animal, please speak to a veterinarian.\n"
                   "To find a local expert near you, use this website: https://vetlocator.com/.")
   else:
    final_output = "good"
   return {"response" : final_output}

def create_conversation(email: str, title: Optional[str] = None) -> str:
    conversation_id = str(uuid.uuid4())
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO conversations (conversation_id, email, title) VALUES (%s, %s, %s)",
            (conversation_id, email, title),
        )
        connection.commit()
    finally:
        connection.close()
    return conversation_id

def conversation_belongs_to(email: str, conversation_id: str) -> bool:
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = %s AND email = %s",
            (conversation_id, email),
        )
        return cursor.fetchone() is not None
    finally:
        connection.close()

def get_session_memory(email: str, conversation_id: str):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT query_number, request_id, raw_question, standalone_question, answer "
            "FROM user_session_memory WHERE email = %s AND conversation_id = %s ORDER BY query_number",
            (email, conversation_id),
        )
        return cursor.fetchall()
    finally:
        connection.close()

def get_conversation_summary(email: str, conversation_id: str):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT summary FROM user_conversation_summary WHERE email = %s AND conversation_id = %s",
            (email, conversation_id),
        )
        row = cursor.fetchone()
        return row[0] if row else ""
    finally:
        connection.close()

def set_conversation_summary(email: str, conversation_id: str, summary: str):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO user_conversation_summary (email, conversation_id, summary) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE summary = %s",
            (email, conversation_id, summary, summary),
        )
        connection.commit()
    finally:
        connection.close()

def append_session_memory(email: str, conversation_id: str, entry: dict):
    return append_turn(_get_db_connection, email, conversation_id, entry)

@app.post("/conversations")
async def new_conversation(email: str = Query(...)):
    return {"conversation_id": create_conversation(email)}

@app.get("/conversations")
async def list_conversations(email: str = Query(...)):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT conversation_id, title, created_at, updated_at FROM conversations WHERE email = %s ORDER BY updated_at DESC", (email,))
        return {"conversations": cursor.fetchall()}
    finally:
        connection.close()

@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, email: str = Query(...)):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM conversations WHERE conversation_id = %s AND email = %s",
            (conversation_id, email),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        connection.commit()
    finally:
        connection.close()
    return {"status": "ok"}

@app.get("/session_memory")
async def read_session_memory(email: str = Query(...), conversation_id: str = Query(...)):
    if not conversation_belongs_to(email, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    entries = get_session_memory(email, conversation_id)
    return {"entries": entries, "count": len(entries), "conversation_summary": get_conversation_summary(email, conversation_id)}

@app.delete("/session_memory")
async def reset_session_memory(email: str = Query(...), conversation_id: str = Query(...)):
    if not conversation_belongs_to(email, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM user_session_memory WHERE email = %s AND conversation_id = %s", (email, conversation_id))
        cursor.execute("DELETE FROM user_conversation_summary WHERE email = %s AND conversation_id = %s", (email, conversation_id))
        cursor.execute(
            "UPDATE conversations SET next_query_number = 1 WHERE email = %s AND conversation_id = %s",
            (email, conversation_id),
        )
        connection.commit()
    finally:
        connection.close()
    return {"status": "ok"}

@app.post("/upload_attachment")
async def upload_attachment(attachment: UploadFile = File(...), email: str = Form(...)):
    file_bytes = await attachment.read()
    attachment_text = extract_text_from_upload(file_bytes, attachment.filename)

    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO user_documents (email, filename, content) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE content = %s",
            (email, attachment.filename, attachment_text, attachment_text)
        )
        connection.commit()
    finally:
        connection.close()
    return JSONResponse({"status": "ok"})

@app.get("/list_attachments")
async def list_attachments(email: str = Query(...)):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT filename FROM user_documents WHERE email = %s", (email,))
        rows = cursor.fetchall()
    finally:
        connection.close()
    return JSONResponse({"documents": [row[0] for row in rows]})

@app.delete("/remove_attachment")
async def remove_attachment(filename: str = Query(...), email: str = Query(...)):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM user_documents WHERE email = %s AND filename = %s", (email, filename))
        connection.commit()
    finally:
        connection.close()
    return JSONResponse({"status": "ok"})

@app.post("/process_query")
async def process_query(background_tasks: BackgroundTasks, query: QueryModel):
    request_id = str(uuid.uuid4())
    conversation_id = query.conversation_id
    if conversation_id and not conversation_belongs_to(query.email, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if not conversation_id:
        conversation_id = create_conversation(query.email, query.user_query[:120])
    update_queues[request_id]
    background_tasks.add_task(process_user_query, query.user_query, request_id, query.email, conversation_id)
    return JSONResponse({"request_id": request_id, "conversation_id": conversation_id})

@app.get("/sse")
async def sse(request_id: str = Query(default=None)):
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")
    return EventSourceResponse(event_generator(request_id))

async def event_generator(request_id: str):
    queue = update_queues[request_id]
    try:
        while True:
            data = await queue.get()
            if isinstance(data, dict) and "end_output" in data:
                yield {"event": "message", "data": json.dumps({"update": data})}
                break
            yield {"event": "message", "data": json.dumps({"update": data})}
    finally:
        update_queues.pop(request_id, None)

def check_attachment_exists(email: str):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM user_documents WHERE email = %s", (email,))
        count = cursor.fetchone()[0]
        return count > 0
    finally:
        connection.close()

def get_user_documents(email: str):
    connection = _get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT filename, content FROM user_documents WHERE email = %s", (email,))
        return {row[0]: row[1] for row in cursor.fetchall()}
    finally:
        connection.close()

def process_user_query(user_query, request_id, email, conversation_id):
    session_memory = get_session_memory(email, conversation_id)
    raw_question = user_query

    if session_memory:
        user_query = generate_standalone_question(user_query, session_memory)
        logging.info(f"[SESSION MEMORY] Standalone question generated: '{user_query}'")

    user_attachment_context = None
    attachment_exist = False
    attachment_based_answer = False

    if check_attachment_exists(email):
        attachment_exist = True
        documents = get_user_documents(email)
        if documents:
            user_attachment_context = "\n\n".join(
                f"Document: {name}\n{content}" for name, content in documents.items()
            )

    attachment_partial_answer = None
    partial_question = None
    if attachment_exist and user_attachment_context:
        can_answer, attachment_answer, question_not_answered = try_answer_from_attachment(user_query, user_attachment_context)
        if can_answer and attachment_answer:
            attachment_based_answer = True
            logging.info("[ATTACHMENT] Fully answered from attachment — skipping PubMed pipeline")
            return_obj = {
                "end_output": attachment_answer,
                "relevant_articles": [],
                "citations_obj": [],
                "citations": [],
                "session_memory_entry": {
                    "request_id": request_id,
                    "raw_question": raw_question,
                    "standalone_question": user_query,
                    "answer": attachment_answer
                }
            }
            append_session_memory(email, conversation_id, return_obj["session_memory_entry"])
            conversation_summary = update_conversation_summary(
                get_conversation_summary(email, conversation_id), user_query, attachment_answer
            )
            set_conversation_summary(email, conversation_id, conversation_summary)
            loop.run_until_complete(send_update(request_id, return_obj))
            return return_obj
        else:
            logging.info("[ATTACHMENT] Attachment insufficient — falling through to PubMed pipeline")
            if attachment_answer:
                attachment_partial_answer = attachment_answer
            if question_not_answered:
                partial_question = question_not_answered
                logging.info(f"[ATTACHMENT] Sending unanswered portion to PubMed: '{partial_question}'")

    pipeline_query = partial_question if attachment_partial_answer and partial_question else user_query

    # Query Generation
    start_poc = time.time()
    general_query, query_contention, query_list = query_generation(pipeline_query)
    end_poc = time.time()

    print("Generated PubMed queries")
    print(query_list)
    loop.run_until_complete(send_update(request_id, "Generated PubMed queries..."))
    # Article Retrieval
    start_api = time.time()
    deduplicated_articles_collected = collect_articles(query_list)
    end_api = time.time()

    print("Retrieved Articles")
    loop.run_until_complete(send_update(request_id, f"Retrieved {len(deduplicated_articles_collected)} Articles..."))
    # Relevance Classifier
    start_relevant = time.time()
    relevant_articles, irrelevant_articles = concurrent_relevance_classification(deduplicated_articles_collected, pipeline_query)
    end_relevant = time.time()

    print("relevant articles")
    loop.run_until_complete(send_update(request_id, f"Classified {len(relevant_articles)} Relevant Articles..."))

    # Article Match
    start_processing = time.time()
    reliability_analysis_df = connect_to_reliability_analysis_db()
    reliability_analysis_df = reliability_analysis_df.where(pd.notnull(reliability_analysis_df), None)
    for col in reliability_analysis_df.select_dtypes(include=np.number).columns:
        reliability_analysis_df[col] = reliability_analysis_df[col].astype(object).where(reliability_analysis_df[col].notnull(), None)
    matched_articles, articles_to_process = article_matching(relevant_articles, reliability_analysis_df)

    print("matched articles")
    # Article Processing
    relevant_article_summaries = concurrent_article_processing(articles_to_process)

    # Write Processed Articles to DB
    write_articles_to_db(relevant_article_summaries, env)

    all_relevant_articles = list(itertools.chain(relevant_article_summaries, matched_articles))
    end_processing = time.time()

    print(f"Processed {len(all_relevant_articles)} Articles...")
    loop.run_until_complete(send_update(request_id, f"Processed {len(all_relevant_articles)} Articles..."))

    # Final Output
    start_output = time.time()
    final_output = generate_final_response(all_relevant_articles, pipeline_query, None)
    if attachment_partial_answer:
        final_output = attachment_partial_answer + "\n\n" + final_output
    end_output = time.time()

    poc_duration = end_poc - start_poc
    api_duration = end_api - start_api
    relevance_classifier_duration = end_relevant - start_relevant
    article_processing_duration = end_processing - start_processing
    final_output_duration = end_output - start_output
    total_runtime = poc_duration + api_duration + article_processing_duration + final_output_duration

    write_output_to_db(user_query, final_output, all_relevant_articles, total_runtime, env)
    end_output = time.time()

    print('-'*200)
    print(final_output)
    print('-'*20)
    print('User Question: ', user_query)
    print('-'*20)
    print('General Query: ', general_query)
    print('-'*20)
    print('Points of Contention: ', query_contention)
    print('-'*20)

    print('# Matched: ', len(matched_articles))
    print('# Processed: ', len(articles_to_process))
    print('# Relevant: ', len(all_relevant_articles))
    print('# Irrelevant: ', len(irrelevant_articles))
    print('Relevant Articles: ', all_relevant_articles)
    print('-'*20)
    print('Total Runtime: ', total_runtime)
    print(' -- ')
    print('[Section 1] Points of Contention: ', poc_duration)
    print('[Section 2] PubMed API Call: ', api_duration)
    print('[Section 3] Relevance Classification: ', relevance_classifier_duration)
    print('[Section 4] Reliability Analysis: ', article_processing_duration)
    print('[Section 5] Final Synthesis: ', final_output_duration)


    return_obj = {
       "end_output": final_output,
       "relevant_articles": all_relevant_articles
    }

    main_output, citations = split_end_output(return_obj["end_output"])
    relevant_articles = return_obj.get("relevant_articles", [])
    updated_citations = match_citations_with_articles(citations, all_relevant_articles)
    return_obj["end_output"] = final_output
    return_obj["citations_obj"] = updated_citations
    return_obj["citations"] = citations
    
    session_memory_entry = {
        "request_id": request_id,
        "raw_question": raw_question,
        "standalone_question": user_query,
        "answer": final_output
    }
    append_session_memory(email, conversation_id, session_memory_entry)
    return_obj["session_memory_entry"] = session_memory_entry
    logging.info(f"[SESSION MEMORY] Entry created | request_id={request_id} | email={email}")

    conversation_summary = update_conversation_summary(
        get_conversation_summary(email, conversation_id), user_query, final_output
    )
    set_conversation_summary(email, conversation_id, conversation_summary)

    loop.run_until_complete(send_update(request_id, return_obj))

    return return_obj

async def send_update(request_id, data):
    if request_id in update_queues:
        await update_queues[request_id].put(data)

async def query_db_final(query: str):
   load_dotenv("ATT81274.env")
   mydb = mysql.connector.connect(
    host=os.getenv('host'),
    port=os.getenv('port'),
    user=os.getenv('user'),
    password=os.getenv('password'),
    database=os.getenv('database')
    )

   try:
      mycursor = mydb.cursor()
      sql = "SELECT * FROM question_answer WHERE question = %s"
      mycursor.execute(sql, (query,))
      return mycursor.fetchall()
   finally:
      mydb.close()


async def sim_score(question: str):
   mydb = mysql.connector.connect(
    host=os.getenv("host"),
    port=os.getenv("port"),
    user=os.getenv("user"),
    password=os.getenv("password"),
    database=os.getenv("database")
  )
   mycursor = mydb.cursor()
   sql = f"SELECT question FROM question_answer;"
   mycursor.execute(sql)
   myresult = mycursor.fetchall()
   resultdict = []

   for x in myresult:
      resultdict.append(x[0])

   if not resultdict:
      return []

   scores_dict = calculate_similarity(resultdict, question)
   print(scores_dict)

   min_heap = []

   for item in scores_dict:
      score = item[0]
      sentence = item[1]
      if (score > 0.23):
         heapq.heappush(min_heap, (score, sentence))
      if (len(min_heap) > 3):
         heapq.heappop(min_heap)
   top_k_sentences = [(score, sentence) for score, sentence in sorted(min_heap, reverse=True)]
   print(top_k_sentences)
   return top_k_sentences

def calculate_similarity(sentences, source_sentence):
    # Combine source sentence with the list of sentences
    all_sentences = sentences + [source_sentence]
    
    # Create the TF-IDF vectorizer and transform the sentences
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(all_sentences)
    
    # Calculate the cosine similarity between the source sentence and all other sentences
    cosine_similarities = cosine_similarity(tfidf_matrix[-1:], tfidf_matrix[:-1]).flatten()
    
    # Combine the similarity scores with the sentences
    similarity_scores = [(score, sentence) for score, sentence in zip(cosine_similarities, sentences)]
    
    return similarity_scores


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)