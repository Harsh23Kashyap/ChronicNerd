from helper_functions import *

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

import asyncio
from sse_starlette.sse import EventSourceResponse
from concurrent.futures import ThreadPoolExecutor

import uuid
import json
import threading
from urllib.parse import unquote

from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from helper_functions import * 

import heapq

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

class QueryModel(BaseModel):
    user_query: str
    session_memory: List[dict] = []

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

@app.get("/db_get/{query:str}")
async def db_get_endpoint(query: str):
   decoded_query = unquote(query)
   result = await query_db_final(decoded_query)

   # A cache hit is still a turn in the conversation, so record it the same way
   # the full pipeline does — otherwise the next follow-up has no context for it.
   cached_answer = None
   if result:
      try:
         cached_answer = json.loads(result[0][1]).get("end_output")
      except (IndexError, TypeError, ValueError) as e:
         logging.info(f"[SESSION MEMORY] /db_get hit but answer could not be parsed: {e}")
   if cached_answer:
      append_session_memory({
         "session_id": str(uuid.uuid4()),
         "raw_question": decoded_query,
         "standalone_question": decoded_query,
         "answer": cached_answer
      })

   return result

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

MEMORY_FILE = "dietnerd_memory.json"
_memory_lock = threading.Lock()

def _default_memory():
    return {"entries": [], "conversation_summary": "", "retrieved_information": ""}

def _load_memory():
    # A missing, empty, or corrupt file means there is no conversation yet — start fresh.
    try:
        with open(MEMORY_FILE, "r") as f:
            memory = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        memory = {}
    if not isinstance(memory, dict):
        memory = {}
    # Backfill keys added after an existing memory file was first written.
    for key, default in _default_memory().items():
        memory.setdefault(key, default)
    if not isinstance(memory["entries"], list):
        memory["entries"] = []
    return memory

def get_session_memory():
    return _load_memory().get("entries", [])

def get_conversation_summary():
    return _load_memory().get("conversation_summary", "")

def set_conversation_summary(summary):
    with _memory_lock:
        memory = _load_memory()
        memory["conversation_summary"] = summary
        with open(MEMORY_FILE, "w") as f:
            json.dump(memory, f, indent=2)
    logging.info(f"[SESSION MEMORY] Conversation summary updated | length={len(summary)}")

def append_session_memory(entry):
    with _memory_lock:
        memory = _load_memory()
        entries = memory.get("entries", [])
        entries.append(entry)
        memory["entries"] = entries
        with open(MEMORY_FILE, "w") as f:
            json.dump(memory, f, indent=2)
    logging.info(f"[SESSION MEMORY] Appended entry to {MEMORY_FILE} | total_entries={len(entries)}")

def clear_session_memory():
    with _memory_lock:
        with open(MEMORY_FILE, "w") as f:
            json.dump(_default_memory(), f, indent=2)
    logging.info(f"[SESSION MEMORY] Cleared {MEMORY_FILE}")

@app.get("/session_memory")
async def read_session_memory():
    memory = _load_memory()
    entries = memory["entries"]
    return JSONResponse({
        "entries": entries,
        "count": len(entries),
        "conversation_summary": memory["conversation_summary"],
        "retrieved_information": memory["retrieved_information"],
    })

@app.delete("/session_memory")
async def reset_session_memory():
    clear_session_memory()
    return JSONResponse({"status": "ok"})

class SessionMemoryCheckModel(BaseModel):
    user_query: str
    session_memory: List[dict] = []

@app.post("/check_session_memory")
async def check_session_memory(body: SessionMemoryCheckModel):
    history = get_session_memory()
    logging.info(f"[SESSION MEMORY] /check_session_memory called | session_memory_empty={len(history) == 0} | history_length={len(history)}")
    if not history:
        logging.info("[SESSION MEMORY] Session memory is EMPTY — skipping check, going to normal flow")
        return JSONResponse({"answered": False, "answer": None})
    standalone_q = generate_standalone_question(body.user_query, history)

    # Answering from session memory is disabled — we only rewrite the question and
    # always fall through to the full pipeline.
    # relevant_context = get_relevant_session_context(standalone_q, history)
    # can_answer, answer = try_answer_from_context(standalone_q, relevant_context)
    can_answer, answer = False, None

    logging.info(f"[SESSION MEMORY] can_answer={can_answer} | standalone_q='{standalone_q}'")
    return JSONResponse({"answered": can_answer, "answer": answer, "standalone_question": standalone_q})

@app.post("/upload_attachment")
async def upload_attachment(attachment: UploadFile = File(...)):
    file_bytes = await attachment.read()
    attachment_text = extract_text_from_upload(file_bytes, attachment.filename)

    try:
        with open("user_documents.json", "r") as f:
            user_documents = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        user_documents = {}

    documents = user_documents.get("documents", {})
    documents[attachment.filename] = attachment_text
    user_documents["documents"] = documents

    with open("user_documents.json", "w") as f:
        json.dump(user_documents, f)
    return JSONResponse({"status": "ok"})

@app.get("/list_attachments")
async def list_attachments():
    try:
        with open("user_documents.json", "r") as f:
            user_documents = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        user_documents = {}
    return JSONResponse({"documents": list(user_documents.get("documents", {}).keys())})

@app.delete("/remove_attachment")
async def remove_attachment(filename: str = Query(...)):
    try:
        with open("user_documents.json", "r") as f:
            user_documents = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        user_documents = {}
    documents = user_documents.get("documents", {})
    documents.pop(filename, None)
    user_documents["documents"] = documents
    with open("user_documents.json", "w") as f:
        json.dump(user_documents, f)
    return JSONResponse({"status": "ok"})

@app.post("/process_query")
async def process_query(background_tasks: BackgroundTasks, query: QueryModel):
    request_id = str(uuid.uuid4())
    update_queues[request_id]  # create the queue before the SSE client connects
    background_tasks.add_task(process_user_query, query.user_query, request_id)
    return JSONResponse({"session_id": request_id})

@app.get("/sse")
async def sse(session_id: str = Query(default=None)):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    return EventSourceResponse(event_generator(session_id))

async def event_generator(session_id: str):
    queue = update_queues[session_id]
    try:
        while True:
            data = await queue.get()
            if isinstance(data, dict) and "final_output" in data:
                yield {"event": "message", "data": json.dumps(data)}
                break
            else:
                yield {"event": "message", "data": json.dumps({"update": data})}
    finally:
        del update_queues[session_id]

def check_attachment_exists():
    try:
        with open("user_documents.json", "r") as f:
            user_documents = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return bool(user_documents.get("documents"))

def process_user_query(user_query, session_id):
    session_memory = get_session_memory()
    raw_question = user_query

    attachment_exist = check_attachment_exists()
    if attachment_exist:
        return process_attachment_query(user_query, session_id)

    if session_memory:
        user_query = generate_standalone_question(user_query, session_memory)
        logging.info(f"[SESSION MEMORY] Standalone question generated: '{user_query}'")

    # Query Generation
    start_poc = time.time()
    general_query, query_contention, query_list = query_generation(user_query)
    end_poc = time.time()

    print("Generated PubMed queries")
    print(query_list)
    loop.run_until_complete(send_update(session_id, "Generated PubMed queries..."))
    # Article Retrieval
    start_api = time.time()
    deduplicated_articles_collected = collect_articles(query_list)
    end_api = time.time()

    print("Retrieved Articles")
    loop.run_until_complete(send_update(session_id, f"Retrieved {len(deduplicated_articles_collected)} Articles..."))
    # Relevance Classifier
    start_relevant = time.time()
    relevant_articles, irrelevant_articles = concurrent_relevance_classification(deduplicated_articles_collected, user_query)
    end_relevant = time.time()

    print("relevant articles")
    loop.run_until_complete(send_update(session_id, f"Classified {len(relevant_articles)} Relevant Articles..."))

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
    loop.run_until_complete(send_update(session_id, f"Processed {len(all_relevant_articles)} Articles..."))

    # Final Output
    start_output = time.time()
    final_output = generate_final_response(all_relevant_articles, user_query, None)
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
        "session_id": session_id,
        "raw_question": raw_question,
        "standalone_question": user_query,
        "answer": final_output
    }
    append_session_memory(session_memory_entry)
    return_obj["session_memory_entry"] = session_memory_entry
    logging.info(f"[SESSION MEMORY] Entry created | session_id={session_id}")

    conversation_summary = update_conversation_summary(
        get_conversation_summary(), user_query, final_output
    )
    set_conversation_summary(conversation_summary)

    loop.run_until_complete(send_update(session_id, return_obj))

    return return_obj

def process_attachment_query(user_query, session_id):
    with open("user_documents.json", "r") as f:
        user_documents = json.load(f)
    documents = user_documents.get("documents", {})
    document_text = "\n\n".join(f"Document: {name}\n{content}" for name, content in documents.items())
    history = user_documents.get("history", [])

    final_output = generate_attachment_response(document_text, history, user_query)

    history.append({"question": user_query, "answer": final_output})
    user_documents["history"] = history
    with open("user_documents.json", "w") as f:
        json.dump(user_documents, f)

    return_obj = {
        "end_output": final_output,
        "relevant_articles": [],
        "citations_obj": [],
        "citations": [],
        "session_memory_entry": {
            "session_id": session_id,
            "raw_question": user_query,
            "standalone_question": user_query,
            "answer": final_output
        }
    }

    loop.run_until_complete(send_update(session_id, return_obj))

    return return_obj

async def send_update(session_id, data):
    if session_id in update_queues:
        await update_queues[session_id].put(data)

async def query_db_final(query: str):
   load_dotenv("ATT81274.env")
   mydb = mysql.connector.connect(
    host=os.getenv('host'),
    port=os.getenv('port'),
    user=os.getenv('user'),
    password=os.getenv('password'),
    database=os.getenv('database')
    )

   mycursor = mydb.cursor()
   sql = f"SELECT * FROM question_answer WHERE question = '{query}'"

   mycursor.execute(sql)

   myresult = mycursor.fetchall()
   with open("output.json", "w") as f:
      json.dump(myresult, f, indent=4)
   return myresult


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