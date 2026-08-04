import os
import requests
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

# Read configurations from environment variables
LABEL_STUDIO_URL = os.getenv("LABEL_STUDIO_URL", "https://label-studio-r4q9.onrender.com")
API_KEY = os.getenv("LABEL_STUDIO_API_KEY")
PHASE_2_PROJECT_ID = os.getenv("PHASE_2_PROJECT_ID")

HEADERS = {
    "Authorization": f"Token {API_KEY}",
    "Content-Type": "application/json"
}

@app.get("/")
def health_check():
    return {"status": "Webhook automation service is online"}

@app.post("/webhook/phase1-complete")
async def handle_phase1_completion(request: Request):
    payload = await request.json()

    event = payload.get("action")
    if event not in ["ANNOTATION_CREATED", "ANNOTATION_UPDATED"]:
        return {"status": "ignored", "reason": f"Event {event} ignored"}

    task_data = payload.get("task", {}).get("data", {})
    annotation_results = payload.get("annotation", {}).get("result", [])

    # Extract score choices
    scores = {}
    for item in annotation_results:
        from_name = item.get("from_name")
        choices = item.get("value", {}).get("choices", [])
        if choices:
            choice_val = choices[0]
            # Extract leading number (e.g. "1 - Extremely Accurate" -> 1)
            score_num = int(choice_val.split("-")[0].strip()) if choice_val[0].isdigit() else 0
            scores[from_name] = score_num

    # Sum penalty scores (Lower score = better)
    acc_a = scores.get("accuracy_a", 1)
    hal_a = scores.get("hallucination_a", 1)
    acc_b = scores.get("accuracy_b", 1)
    hal_b = scores.get("hallucination_b", 1)

    score_a = acc_a + hal_a
    score_b = acc_b + hal_b

    # Select winning summary
    if score_a <= score_b:
        chosen_summary = task_data.get("summary_a_text")
        chosen_label = "A"
    else:
        chosen_summary = task_data.get("summary_b_text")
        chosen_label = "B"

    # Create task payload for Phase 2
    phase2_payload = {
        "data": {
            "packet_id": task_data.get("packet_id"),
            "title": task_data.get("title"),
            "intro_text": task_data.get("intro_text"),
            "chosen_summary_source": chosen_label,
            "selected_summary": chosen_summary
        }
    }

    res = requests.post(
        f"{LABEL_STUDIO_URL}/api/projects/{PHASE_2_PROJECT_ID}/tasks",
        headers=HEADERS,
        json=phase2_payload
    )

    if res.status_code in [200, 201]:
        return {"status": "success", "message": "Pushed winning summary to Phase 2"}
    else:
        raise HTTPException(status_code=500, detail=f"Label Studio API error: {res.text}")