import os
import random
import logging
import requests
from fastapi import FastAPI, Request, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook")

app = FastAPI()

LABEL_STUDIO_URL = os.getenv("LABEL_STUDIO_URL", "https://label-studio-r4q9.onrender.com")
API_KEY = os.getenv("LABEL_STUDIO_API_KEY")
PHASE_2_PROJECT_ID = os.getenv("PHASE_2_PROJECT_ID")

HEADERS = {
    "Authorization": f"Token {API_KEY}",
    "Content-Type": "application/json"
}

def extract_score(result_list, target_name):
    """Extracts numerical rating from question responses (e.g. '5 - Excellent' -> 5)."""
    if not isinstance(result_list, list):
        return 0
    for res in result_list:
        if isinstance(res, dict) and res.get("from_name") == target_name:
            choices = res.get("value", {}).get("choices", [])
            if choices and choices[0] and str(choices[0])[0].isdigit():
                return int(str(choices[0])[0])
    return 0

@app.get("/")
def health_check():
    return {"status": "Webhook online"}

@app.post("/webhook/phase1-complete")
async def handle_phase1_completion(request: Request):
    try:
        payload = await request.json()
    except Exception as parse_err:
        return {"status": "error", "message": f"Invalid JSON payload: {parse_err}"}

    action = payload.get("action", "")

    # Only process annotation actions
    if action not in ["ANNOTATION_CREATED", "ANNOTATION_UPDATED"]:
        return {"status": "ignored", "reason": f"Action '{action}' ignored"}

    try:
        task = payload.get("task", {})
        task_data = task.get("data", {}) if isinstance(task, dict) else {}

        # Safely extract annotation result list regardless of webhook payload structure
        annotation = payload.get("annotation", {})
        if isinstance(annotation, dict) and "result" in annotation:
            annotation_results = annotation.get("result", [])
        elif "annotations" in payload and isinstance(payload["annotations"], list) and payload["annotations"]:
            annotation_results = payload["annotations"][0].get("result", [])
        else:
            annotation_results = []

        # Extract overall quality scores (Q5)
        score_a_quality = extract_score(annotation_results, "a_q5")
        score_b_quality = extract_score(annotation_results, "b_q5")

        # Extract metric breakdowns matching export script schema
        metrics_a = {
            "accuracy": extract_score(annotation_results, "a_q1"),
            "hallucinations": extract_score(annotation_results, "a_q2"),
            "fluency": extract_score(annotation_results, "a_q3"),
            "completeness": extract_score(annotation_results, "a_q4"),
            "quality": score_a_quality,
        }

        metrics_b = {
            "accuracy": extract_score(annotation_results, "b_q1"),
            "hallucinations": extract_score(annotation_results, "b_q2"),
            "fluency": extract_score(annotation_results, "b_q3"),
            "completeness": extract_score(annotation_results, "b_q4"),
            "quality": score_b_quality,
        }

        # Winner selection logic
        if score_a_quality > score_b_quality:
            winner_label = "Summary A"
            winning_text = task_data.get("summary_a_text", "")
        elif score_b_quality > score_a_quality:
            winner_label = "Summary B"
            winning_text = task_data.get("summary_b_text", "")
        else:
            choice = random.choice(["A", "B"])
            winner_label = f"Tie (Selected Summary {choice})"
            winning_text = task_data.get("summary_a_text", "") if choice == "A" else task_data.get("summary_b_text", "")

        # Format payload for Phase 2 task creation
        phase2_payload = {
            "data": {
                "packet_id": task_data.get("packet_id", ""),
                "title": task_data.get("title", ""),
                "intro_text": task_data.get("intro_text", ""),
                "winning_summary_text": winning_text,
                "winner_label": winner_label,
                "summary_a_metrics": metrics_a,
                "summary_b_metrics": metrics_b,
            }
        }

        target_url = f"{LABEL_STUDIO_URL.rstrip('/')}/api/projects/{PHASE_2_PROJECT_ID}/tasks"
        res = requests.post(target_url, headers=HEADERS, json=phase2_payload)

        return {
            "status": "success",
            "ls_status_code": res.status_code,
            "ls_response": res.text,
            "winner": winner_label
        }

    except Exception as e:
        # Prevent 500 error and return exception details cleanly
        return {
            "status": "error",
            "exception": str(e)
        }