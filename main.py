import os
import logging
import requests
from fastapi import FastAPI, Request

# Configure logging to see output directly in Render logs
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

@app.get("/")
def health_check():
    return {"status": "Webhook online"}

@app.post("/webhook/phase1-complete")
async def handle_phase1_completion(request: Request):
    try:
        payload = await request.json()
        
        # Log the action
        action = payload.get("action")
        logger.info(f"--- WEBHOOK TRIGGERED: Action = {action} ---")

        # Ignore non-annotation events
        if action not in ["ANNOTATION_CREATED", "ANNOTATION_UPDATED"]:
            logger.info(f"Ignoring action: {action}")
            return {"status": "ignored", "reason": f"Action {action} ignored"}

        # Extract task and annotation
        task_data = payload.get("task", {}).get("data", {})
        annotation_results = payload.get("annotation", {}).get("result", [])

        logger.info(f"Task Data Keys: {list(task_data.keys())}")
        logger.info(f"Annotation Results Count: {len(annotation_results)}")

        # Safely extract scores
        scores = {}
        for item in annotation_results:
            from_name = item.get("from_name")
            value = item.get("value", {})
            choices = value.get("choices", [])
            if choices:
                choice_val = str(choices[0])
                # Safely attempt to convert leading character to integer
                first_char = choice_val.split("-")[0].strip()
                score_num = int(first_char) if first_char.isdigit() else 0
                scores[from_name] = score_num

        logger.info(f"Extracted Scores: {scores}")

        # Compute total scores (Default to 1 if key missing)
        score_a = scores.get("accuracy_a", 1) + scores.get("hallucination_a", 1)
        score_b = scores.get("accuracy_b", 1) + scores.get("hallucination_b", 1)

        # Retrieve text with fallbacks
        summary_a = task_data.get("summary_a_text") or task_data.get("summary_a") or ""
        summary_b = task_data.get("summary_b_text") or task_data.get("summary_b") or ""

        if score_a <= score_b:
            chosen_summary = summary_a
            chosen_label = "A"
        else:
            chosen_summary = summary_b
            chosen_label = "B"

        # Build payload for Phase 2
        phase2_payload = {
            "data": {
                "packet_id": task_data.get("packet_id"),
                "title": task_data.get("title"),
                "intro_text": task_data.get("intro_text"),
                "chosen_summary_source": chosen_label,
                "selected_summary": chosen_summary
            }
        }

        # Make request to Label Studio Phase 2
        target_url = f"{LABEL_STUDIO_URL.rstrip('/')}/api/projects/{PHASE_2_PROJECT_ID}/tasks"
        logger.info(f"Posting winning task to: {target_url}")

        res = requests.post(target_url, headers=HEADERS, json=phase2_payload)

        logger.info(f"Label Studio Response Status: {res.status_code}")
        logger.info(f"Label Studio Response Body: {res.text}")

        return {
            "status": "processed", 
            "ls_status": res.status_code, 
            "ls_response": res.text
        }

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return {"status": "error", "message": str(e)}