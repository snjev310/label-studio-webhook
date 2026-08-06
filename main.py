import os
import json
import random
import logging
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from label_studio_sdk import LabelStudio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook")

app = FastAPI()

# Environment Variables
LABEL_STUDIO_URL = os.getenv("LABEL_STUDIO_URL", "https://label-studio-r4q9.onrender.com").rstrip("/")
API_KEY = os.getenv("LABEL_STUDIO_API_KEY", "").strip()
PHASE_2_PROJECT_ID = int(os.getenv("PHASE_2_PROJECT_ID", "2"))

# Initialize official LabelStudio Client
ls_client = LabelStudio(
    base_url=LABEL_STUDIO_URL,
    api_key=API_KEY
)


def parse_choice_type(result_list, target_name):
    """
    Extracts choice classification ('A', 'B', or 'C') for a given target control name
    (e.g., 'a_simplicity' or 'b_simplicity').
    """
    if not isinstance(result_list, list):
        return None
    for res in result_list:
        if isinstance(res, dict) and res.get("from_name") == target_name:
            choices = res.get("value", {}).get("choices", [])
            if choices and choices[0]:
                choice_str = str(choices[0]).strip().lower()
                if choice_str.startswith("a lot"):
                    return "A"
                elif choice_str.startswith("little"):
                    return "B"
                elif choice_str.startswith("some"):
                    return "C"
    return None


def extract_text_field(result_list, target_name):
    """Extracts text inputs provided by annotators (e.g., line numbers)."""
    if not isinstance(result_list, list):
        return ""
    for res in result_list:
        if isinstance(res, dict) and res.get("from_name") == target_name:
            text_values = res.get("value", {}).get("text", [])
            if text_values:
                return text_values[0]
    return ""


def flatten_questions(questions):
    """
    Ensures questions is a native Python list (parsing JSON strings if needed)
    and flattens nested MCQ options into direct keys for Label Studio Repeater.
    """
    if questions is None:
        return []

    # 1. CRITICAL FIX: If questions is received as a JSON string, parse it into a list
    if isinstance(questions, str):
        try:
            questions = json.loads(questions)
        except Exception as e:
            logger.error(f"Failed to parse questions JSON string: {e}")
            return []

    flattened = []
    if not isinstance(questions, list):
        logger.warning(f"Expected questions to be a list, but got: {type(questions)}")
        return flattened

    # 2. Loop through each question dictionary and flatten options
    for q in questions:
        if not isinstance(q, dict):
            continue
        
        q_text = q.get("question", "")
        options = q.get("options", [])
        
        opt_texts = []
        for opt in options:
            if isinstance(opt, dict):
                opt_texts.append(opt.get("text", ""))
            else:
                opt_texts.append(str(opt))
        
        # Ensure exactly 5 option entries exist (A through E)
        while len(opt_texts) < 5:
            opt_texts.append("N/A")

        flattened.append({
            "question": q_text,
            "option_a": opt_texts[0],
            "option_b": opt_texts[1],
            "option_c": opt_texts[2],
            "option_d": opt_texts[3],
            "option_e": opt_texts[4],
        })

    return flattened


@app.get("/")
def health_check():
    return {"status": "Webhook online"}


@app.get("/webhook/phase1-complete")
def webhook_get_check():
    return {"status": "Webhook endpoint is active (POST required)"}


@app.post("/webhook/phase1-complete")
async def handle_phase1_completion(request: Request):
    try:
        payload = await request.json()
        action = payload.get("action", "")
        logger.info(f"Incoming Webhook Event: {action}")

        # Process only active annotation events
        if action not in ["ANNOTATION_CREATED", "ANNOTATION_UPDATED", "annotation_created", "annotation_updated"]:
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": f"Action {action} skipped"})

        task = payload.get("task", {})
        task_data = task.get("data", {}) if isinstance(task, dict) else {}

        # ROBUST RESULT EXTRACTION:
        # Handles all Label Studio webhook versions (root level, payload.annotation, or payload.annotations[0])
        results = []
        if "result" in payload and isinstance(payload["result"], list):
            results = payload["result"]
        elif "annotation" in payload and isinstance(payload["annotation"], dict) and "result" in payload["annotation"]:
            results = payload["annotation"]["result"]
        elif "annotations" in payload and isinstance(payload["annotations"], list) and payload["annotations"]:
            results = payload["annotations"][0].get("result", [])

        # Parse Simplicity choices for Summary A and Summary B
        choice_a = parse_choice_type(results, "a_simplicity")
        choice_b = parse_choice_type(results, "b_simplicity")

        logger.info(f"Task {task.get('id')}: Summary A Choice = '{choice_a}', Summary B Choice = '{choice_b}'")

        summary_a = task_data.get("summary_a_text", "")
        summary_b = task_data.get("summary_b_text", "")

        winning_text = None
        winner_label = None

        # Logic: Option B ("Little to no jargon...") designates a clear winner
        if choice_a == "B" and choice_b != "B":
            winning_text = summary_a
            winner_label = "Summary A"
        elif choice_b == "B" and choice_a != "B":
            winning_text = summary_b
            winner_label = "Summary B"
        elif choice_a == "B" and choice_b == "B":
            # Tie-breaker: default to Summary A if both are rated B
            winning_text = summary_a
            winner_label = "Summary A (Tie-breaker)"

        # If no summary was marked B, skip forwarding
        if not winning_text:
            logger.info("No clear winner (Option B) selected in Phase 1. Skipping Phase 2 task creation.")
            return JSONResponse(status_code=200, content={"status": "skipped", "reason": "No clear winner chosen"})

        # Collect feedback line numbers for auditability
        feedback = {
            "a_simplicity": choice_a,
            "b_simplicity": choice_b,
            "a_jargon_lines": extract_text_field(results, "a_jargon_lines"),
            "a_incomprehensible_lines": extract_text_field(results, "a_incomprehensible_lines"),
            "a_hallucination_lines": extract_text_field(results, "a_hallucination_lines"),
            "b_jargon_lines": extract_text_field(results, "b_jargon_lines"),
            "b_incomprehensible_lines": extract_text_field(results, "b_incomprehensible_lines"),
            "b_hallucination_lines": extract_text_field(results, "b_hallucination_lines"),
        }

        # Build payload for Phase 2 creation
        phase2_data = {
            "packet_id": task_data.get("packet_id", ""),
            "title": task_data.get("title", ""),
            "winning_summary_text": winning_text,
            "questions": flatten_questions(task_data.get("questions", [])),
            "winner_label": winner_label,
            "phase1_feedback": feedback
        }

        # Create task in Phase 2 using the official Label Studio SDK
        created_task = ls_client.tasks.create(
            project=PHASE_2_PROJECT_ID,
            data=phase2_data
        )

        logger.info(f"Successfully created Phase 2 Task ID: {created_task.id}")

        return JSONResponse(status_code=200, content={
            "status": "success",
            "task_id": created_task.id,
            "winner_label": winner_label
        })

    except Exception as e:
        logger.error(f"Webhook processing error: {e}\n{traceback.format_exc()}")
        return JSONResponse(status_code=200, content={"status": "error", "message": str(e)})