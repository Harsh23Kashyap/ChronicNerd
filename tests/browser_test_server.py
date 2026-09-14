"""Local browser-E2E server: real app/DB/SSE with only external science APIs stubbed."""
import sys

import uvicorn

sys.path.insert(0, "dietnerd-backend")
import main


def summary(previous, question, answer):
    return ((previous + "\n") if previous else "") + question + ": " + answer


def fake_process(user_query, request_id, email, conversation_id):
    memory = main.get_session_memory(email, conversation_id)
    standalone = user_query
    if memory and user_query.lower() == "what about sleep?":
        standalone = "What are the effects of magnesium on sleep?"
    answer = "Generated browser-test answer for: " + standalone
    entry = {
        "request_id": request_id,
        "raw_question": user_query,
        "standalone_question": standalone,
        "answer": answer,
    }
    main.append_session_memory(email, conversation_id, entry)
    main.set_conversation_summary(
        email,
        conversation_id,
        summary(main.get_conversation_summary(email, conversation_id), standalone, answer),
    )
    main.loop.run_until_complete(main.send_update(request_id, "Generated PubMed queries..."))
    main.loop.run_until_complete(main.send_update(request_id, {
        "end_output": answer,
        "relevant_articles": [],
        "citations_obj": {},
        "citations": [],
        "session_memory_entry": entry,
    }))


main.process_user_query = fake_process
main.determine_question_validity = lambda question: "True"
main.update_conversation_summary = summary

if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=8000)
