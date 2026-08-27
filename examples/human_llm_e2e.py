"""Real HTTP smoke test using the official OpenAI Python client.

Required environment variables:
  HUMAN_API_BASE_URL (for example http://localhost:8080)
  HUMAN_API_KEY
  RESPONDER_EMAIL
  RESPONDER_PASSWORD
"""
import os
import threading
import time

import httpx
from openai import OpenAI

base=os.environ.get("HUMAN_API_BASE_URL","http://localhost:8080").rstrip("/")
api_key=os.environ["HUMAN_API_KEY"]
email=os.environ["RESPONDER_EMAIL"]
password=os.environ["RESPONDER_PASSWORD"]
session=httpx.Client(base_url=base,timeout=10,trust_env=False)
login=session.post("/api/auth/login",json={"email":email,"password":password}); login.raise_for_status()
csrf=login.json()["csrf_token"]; headers={"X-CSRF-Token":csrf}
session.post("/api/human/heartbeat",headers=headers).raise_for_status()
result={}

def call_api():
    client=OpenAI(api_key=api_key,base_url=base+"/v1",timeout=60,http_client=httpx.Client(trust_env=False))
    result["response"]=client.chat.completions.create(model="human-1",messages=[{"role":"user","content":"Human LLM end-to-end test"}],extra_headers={"Idempotency-Key":"documented-e2e"})

thread=threading.Thread(target=call_api,daemon=True); thread.start()
question=None
for _ in range(100):
    queue=session.get("/api/human/questions",params={"scope":"available"}); queue.raise_for_status()
    question=next((item for item in queue.json()["data"] if item["messages"][-1]["content"]=="Human LLM end-to-end test"),None)
    if question: break
    time.sleep(.2)
assert question,"Question did not appear"
visible_id=question["completion_id"]
session.post(f"/api/human/questions/{question['id']}/claim",headers=headers).raise_for_status()
invalid=session.post(f"/api/human/questions/{question['id']}/answer",headers=headers,json={"id":"chatcmpl_attacker_controlled","content":"Invalid"})
assert invalid.status_code==422
session.post(f"/api/human/questions/{question['id']}/answer",headers=headers,json={"content":"HUMAN_E2E_RESPONSE_OK"}).raise_for_status()
thread.join(60); response=result["response"]
assert response.id==visible_id and response.id.startswith("chatcmpl_")
assert response.object=="chat.completion" and response.model=="human-1"
assert response.choices[0].index==0 and response.choices[0].message.role=="assistant"
assert response.choices[0].message.content=="HUMAN_E2E_RESPONSE_OK"
assert response.choices[0].finish_reason=="stop"
print("HUMAN_LLM_E2E_PASS")
print("ID shown == ID returned:",visible_id)
