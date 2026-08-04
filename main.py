import os
import random
import logging
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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
    """Extracts numerical rating from question choices safely."""
    if not isinstance(result_list, list):
        return 0
    for res in result_list:
        if isinstance(res, dict) and res.get("from_name") == target_name:
            choices = res.get("value", {}).get("choices", [])
            if choices and len(choices) > 0 and choices[0]:
                val_str = str(choices[0]).strip()
                if val_str and val_str[0].isdigit():
                    return int(val_str[0])
    return 0

def extract_overall_winner(result_list):
    """Extracts explicit human choice from overall_winner if present."""
    if not isinstance(result_list, list):
        return None
    for res in result_list:
        if isinstance(res, dict) and res.get("from_name") == "overall_winner":
            choices = res.get("value", {}).get("choices", [])
            if choices and len(choices) > 0 and choices[0]:
                val = str(choices[0])
                if "Summary A" in val:
                    return "Summary A"
                elif "Summary B" in val:
                    return "Summary B"
                elif "Tie" in val:
                    return "Tie"
    return None

@app.get("/")
def health_check():
    return {"status": "Webhook online"}

@app.post("/webhook/phase1-complete")
async def handle_phase1_completion(request: Request):
    try:
        # Read raw json body safely
        try:
            payload = await request.json()
        except Exception as json_err:
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": f"Invalid JSON body: {str(json_err)}"})

        action = payload.get("action", "")

        # Ignore non-annotation events safely
        if action not in ["ANNOTATION_CREATED", "ANNOTATION_UPDATED"]:
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": f"Action '{action}' ignored"})

        task = payload.get("task", {})
        task_data = task.get("data", {}) if isinstance(task, dict) else {}

        # Safely extract annotation result list regardless of structure
        annotation = payload.get("annotation", {})
        if isinstance(annotation, dict) and "result" in annotation:
            annotation_results = annotation.get("result", [])
        elif "annotations" in payload and isinstance(payload["annotations"], list) and len(payload["annotations"]) > 0:
            annotation_results = payload["annotations"][0].get("result", [])
        else:
            annotation_results = []

        # Extract Quality Scores (Q5)
        score_a_quality = extract_score(annotation_results, "a_q5")
        score_b_quality = extract_score(annotation_results, "b_q5")

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

        human_winner = extract_overall_winner(annotation_results)

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

        return JSONResponse(status_code=200, content={
            "status": "success",
            "ls_status": res.status_code,
            "winner_label": winner_label
        })

    except Exception as top_err:
        # Guarantee a 200 OK response with the exact error details inside JSON
        return JSONResponse(status_code=200, content={
            "status": "error_caught",
            "error_message": str(top_err)
        })