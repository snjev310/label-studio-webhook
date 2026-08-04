import os
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

def extract_score(result_list, target_name):
    """Safely extracts numerical ratings matching choice strings (e.g. '5 - Excellent' -> 5)."""
    if not isinstance(result_list, list):
        return 0
    for res in result_list:
        if isinstance(res, dict) and res.get("from_name") == target_name:
            choices = res.get("value", {}).get("choices", [])
            if choices and choices[0]:
                val_str = str(choices[0]).strip()
                if val_str and val_str[0].isdigit():
                    return int(val_str[0])
    return 0

def extract_overall_winner(result_list):
    """Extracts explicit human selection from 'overall_winner' if recorded."""
    if not isinstance(result_list, list):
        return None
    for res in result_list:
        if isinstance(res, dict) and res.get("from_name") == "overall_winner":
            choices = res.get("value", {}).get("choices", [])
            if choices and choices[0]:
                val_str = str(choices[0])
                if "Summary A" in val_str:
                    return "Summary A"
                elif "Summary B" in val_str:
                    return "Summary B"
    return None

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
        if action not in ["ANNOTATION_CREATED", "ANNOTATION_UPDATED"]:
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": f"Action {action} skipped"})

        task = payload.get("task", {})
        task_data = task.get("data", {}) if isinstance(task, dict) else {}

        # Safely extract annotation result list
        annotation = payload.get("annotation", {})
        if isinstance(annotation, dict) and "result" in annotation:
            results = annotation.get("result", [])
        elif "annotations" in payload and isinstance(payload["annotations"], list) and payload["annotations"]:
            results = payload["annotations"][0].get("result", [])
        else:
            results = []

        # Extract quality scores
        score_a_quality = extract_score(results, "a_q5")
        score_b_quality = extract_score(results, "b_q5")

        metrics_a = {
            "accuracy": extract_score(results, "a_q1"),
            "hallucinations": extract_score(results, "a_q2"),
            "fluency": extract_score(results, "a_q3"),
            "completeness": extract_score(results, "a_q4"),
            "quality": score_a_quality,
        }

        metrics_b = {
            "accuracy": extract_score(results, "b_q1"),
            "hallucinations": extract_score(results, "b_q2"),
            "fluency": extract_score(results, "b_q3"),
            "completeness": extract_score(results, "b_q4"),
            "quality": score_b_quality,
        }

        # Check explicit choice -> overall quality score -> tie-breaker
        human_winner = extract_overall_winner(results)
        summary_a = task_data.get("summary_a_text", "")
        summary_b = task_data.get("summary_b_text", "")

        if human_winner == "Summary A":
            winner_label = "Summary A"
            winning_text = summary_a
        elif human_winner == "Summary B":
            winner_label = "Summary B"
            winning_text = summary_b
        elif score_a_quality > score_b_quality:
            winner_label = "Summary A"
            winning_text = summary_a
        elif score_b_quality > score_a_quality:
            winner_label = "Summary B"
            winning_text = summary_b
        else:
            choice = random.choice(["A", "B"])
            winner_label = f"Tie (Selected Summary {choice})"
            winning_text = summary_a if choice == "A" else summary_b

        phase2_data = {
            "packet_id": task_data.get("packet_id", ""),
            "title": task_data.get("title", ""),
            "intro_text": task_data.get("intro_text", ""),
            "winning_summary_text": winning_text,
            "winner_label": winner_label,
            "summary_a_metrics": metrics_a,
            "summary_b_metrics": metrics_b,
        }

        # Create task in Phase 2 using official SDK method
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