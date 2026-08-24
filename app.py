#!/usr/bin/env python3
import os
import time
import threading
import requests
import json
import re
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

app = Flask(__name__)

AI_BRAIN_URL     = "https://synapse-ledger.lovable.app"
AI_BRAIN_API_KEY = "abrn_agt0004_eddc5d433291f12e976726836d2c5a07636076d353acd221"
NTFY_TOPIC       = "maat-atlas-wake"
CHECK_INTERVAL   = 300
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL   = "deepseek-chat"

HEARTBEATS       = {}
MESSAGES         = []
msg_counter      = 0
AGENT_CARDS      = {}
AGENT_TRUST      = {}
DIRECTIVES       = []
SKILL_RECS       = []
SWARM_HEALTH     = []
CONSTITUTION     = []
KNOWLEDGE_GRAPH  = []
FAILURE_LOG      = []
ATLAS_INSIGHTS   = []
directive_counter  = 0
kg_counter         = 0
insight_counter    = 0
ATLAS_AUTONOMY     = 1
last_alert_hermes   = None
last_alert_openclaw = None
last_alert_aibrain  = None
last_alert_termux   = None

def log(msg):
    print(f"[{datetime.utcnow().isoformat()}] {msg}")

def deepseek_ask(prompt, max_tokens=150):
    if not DEEPSEEK_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": DEEPSEEK_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            timeout=15
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"DeepSeek error: {e}")
    return None

def extract_json(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            inner = parts[1].strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
            text = inner
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        return json.loads(text.strip())
    except Exception:
        return None

def send_ntfy(title, message, priority="high"):
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message,
            headers={"Title": title, "Priority": priority, "Tags": "warning"},
            timeout=10
        )
        log(f"ntfy sent: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        log(f"ntfy failed: {e}")
        return False

def aibrain_get(endpoint):
    if not AI_BRAIN_URL or not AI_BRAIN_API_KEY:
        return None
    try:
        r = requests.get(f"{AI_BRAIN_URL}{endpoint}", headers={"x-api-key": AI_BRAIN_API_KEY}, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        log(f"AI Brain GET failed: {e}")
        return None

def aibrain_post(endpoint, payload):
    if not AI_BRAIN_URL or not AI_BRAIN_API_KEY:
        return None
    try:
        r = requests.post(
            f"{AI_BRAIN_URL}{endpoint}",
            headers={"x-api-key": AI_BRAIN_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        return r.json() if r.status_code in (200, 201) else None
    except Exception as e:
        log(f"AI Brain POST failed: {e}")
        return None

def check_agent(agent_name):
    hb = HEARTBEATS.get(agent_name)
    if not hb:
        return {"alive": False, "error": "no heartbeat", "downtime_seconds": 999999}
    try:
        last = datetime.fromisoformat(hb["last_seen"].replace("Z", "+00:00"))
        downtime = datetime.now().astimezone() - last
        return {"alive": downtime < timedelta(minutes=10), "last_seen": hb["last_seen"], "downtime_seconds": downtime.total_seconds(), "status": hb.get("status", "unknown")}
    except Exception as e:
        return {"alive": False, "error": str(e), "downtime_seconds": 999999}

def update_agent_trust(agent_name):
    global AGENT_TRUST
    prompt = f"Score {agent_name}'s reliability 0-100 based on recent uptime. Return ONLY JSON: {{\"trust_score\": number, \"completion_rate\": number, \"reason\": string}}"
    result = deepseek_ask(prompt, 150)
    parsed = extract_json(result)
    if not parsed:
        parsed = {"trust_score": 50, "completion_rate": 0.5, "reason": "No data or parse failed"}
    ts = parsed.get("trust_score", 50)
    AGENT_TRUST[agent_name] = {
        "trust_score": ts,
        "autonomy_level": 0 if ts < 30 else (1 if ts < 60 else (2 if ts < 80 else 3)),
        "completion_rate": parsed.get("completion_rate", 0.5),
        "reason": parsed.get("reason", ""),
        "last_updated": datetime.utcnow().isoformat()
    }
    aibrain_post("/api/public/v1/agent_trust", {"agent": agent_name, "trust_score": AGENT_TRUST[agent_name]["trust_score"], "autonomy_level": AGENT_TRUST[agent_name]["autonomy_level"], "completion_rate": AGENT_TRUST[agent_name]["completion_rate"], "last_updated": AGENT_TRUST[agent_name]["last_updated"]})
    log(f"Trust for {agent_name}: score={ts}, level={AGENT_TRUST[agent_name]['autonomy_level']}")

def calculate_swarm_health():
    global SWARM_HEALTH
    hermes = check_agent("hermes")
    openclaw = check_agent("openclaw")
    coord = 100 if (hermes.get("alive") and openclaw.get("alive")) else (50 if (hermes.get("alive") or openclaw.get("alive")) else 0)
    momentum = 50 + (25 if hermes.get("alive") else 0) + (25 if openclaw.get("alive") else 0)
    stale = []
    projects = aibrain_get("/api/public/v1/projects")
    if projects:
        for p in projects:
            updated = p.get("updated_at") or p.get("last_activity")
            if updated:
                try:
                    last = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if (datetime.now().astimezone() - last) > timedelta(days=10):
                        stale.append(p.get("name", "unknown"))
                except Exception:
                    pass
    overall = max(0, min(100, (coord + momentum) / 2 - len(stale) * 10))
    health = {"date": datetime.utcnow().isoformat(), "coordination_score": coord, "momentum_score": momentum, "stale_projects": stale, "overall_score": overall}
    SWARM_HEALTH.append(health)
    aibrain_post("/api/public/v1/swarm_health", health)
    log(f"Swarm health: overall={overall}, stale={len(stale)}")
    return health

def generate_insight():
    global ATLAS_INSIGHTS, insight_counter
    stale = []
    projects = aibrain_get("/api/public/v1/projects")
    if projects:
        for p in projects:
            updated = p.get("updated_at") or p.get("last_activity")
            if updated:
                try:
                    last = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if (datetime.now().astimezone() - last) > timedelta(days=10):
                        stale.append(f"{p.get('name', 'unknown')}: {p.get('status', 'no status')}")
                except Exception:
                    pass
    prompt = f"Given stale projects: {stale}, write ONE JSON: {{\"title\": string, \"observation\": string, \"evidence\": string, \"suggested_action\": string, \"confidence\": number 0-1}}"
    result = deepseek_ask(prompt, 250)
    parsed = extract_json(result)
    if not parsed:
        parsed = {"title": "No insight", "observation": "No data or parse failed", "evidence": "", "suggested_action": "Wait for more data", "confidence": 0.0}
    insight_counter += 1
    insight = {"id": insight_counter, "title": parsed.get("title", "Untitled"), "observation": parsed.get("observation", ""), "evidence": parsed.get("evidence", ""), "suggested_action": parsed.get("suggested_action", ""), "confidence": parsed.get("confidence", 0.0), "created_at": datetime.utcnow().isoformat()}
    ATLAS_INSIGHTS.append(insight)
    aibrain_post("/api/public/v1/atlas_insights", insight)
    log(f"Insight #{insight_counter}: {insight['title']} (confidence: {insight['confidence']})")
    if insight["confidence"] > 0.7:
        send_ntfy(f"Atlas Insight: {insight['title']}", f"{insight['observation']}\n💡 {insight['suggested_action']}\nConfidence: {insight['confidence']}", "default")
    return insight

def decay_field_strengths():
    entries = aibrain_get("/api/public/v1/knowledge_graph") or []
    for e in entries:
        try:
            last = datetime.fromisoformat(e["last_reinforced"].replace("Z", "+00:00"))
            age_days = (datetime.now().astimezone() - last).days
            new_strength = max(1, e.get("strength", 5) - age_days * 0.5)
            aibrain_post(f"/api/public/v1/knowledge_graph/{e.get('id', '')}", {"strength": new_strength})
        except Exception as ex:
            log(f"Decay skip: {ex}")
    log(f"Knowledge decay: {len(entries)} entries processed")

def seed_constitution():
    global CONSTITUTION
    now = datetime.utcnow().isoformat()
    rules = [
        {"section": "1.1", "rule": "Atlas observes, learns, recommends. Never executes.", "category": "boundary", "priority": "critical", "last_referenced": now},
        {"section": "1.2", "rule": "Atlas never competes with Hermes or OpenClaw for tasks.", "category": "boundary", "priority": "critical", "last_referenced": now},
        {"section": "1.3", "rule": "Every claim carries a confidence score 0.0-1.0.", "category": "identity", "priority": "critical", "last_referenced": now},
        {"section": "2.1", "rule": "Autonomy is earned via agent_trust, never assumed.", "category": "escalation", "priority": "critical", "last_referenced": now},
        {"section": "2.2", "rule": "Level 0: observe. Level 1: suggest. Level 2: direct with reasoning. Level 3: sandbox. Level 4: full autonomy on reversible actions.", "category": "escalation", "priority": "normal", "last_referenced": now},
        {"section": "3.1", "rule": "Atlas is the bridge between Hermes and OpenClaw, not a replacement.", "category": "identity", "priority": "critical", "last_referenced": now},
    ]
    CONSTITUTION = rules
    for r in rules:
        aibrain_post("/api/public/v1/constitution", r)
    log(f"Constitution seeded: {len(rules)} rules")

def watcher_loop():
    global last_alert_hermes, last_alert_openclaw, last_alert_aibrain, last_alert_termux
    log("Atlas v3.0 watcher started")
    seed_constitution()
    update_agent_trust("hermes")
    update_agent_trust("openclaw")
    insight_counter_local = 0
    while True:
        log("Running checks...")
        now = datetime.now()
        aibrain_ok = False
        try:
            r = requests.get(f"{AI_BRAIN_URL}/api/public/v1/health", headers={"x-api-key": AI_BRAIN_API_KEY}, timeout=10)
            aibrain_ok = r.status_code == 200
        except Exception as e:
            log(f"AI Brain check failed: {e}")
        if not aibrain_ok:
            if not last_alert_aibrain or (now - last_alert_aibrain) > timedelta(minutes=30):
                send_ntfy("Atlas Alert", "AI Brain unreachable. Bridge offline.", "urgent")
                last_alert_aibrain = now
        else:
            last_alert_aibrain = None
        hermes = check_agent("hermes")
        log(f"Hermes: alive={hermes.get('alive')}, downtime={hermes.get('downtime_seconds', 0):.0f}s")
        if not hermes.get("alive"):
            if not last_alert_hermes or (now - last_alert_hermes) > timedelta(minutes=30):
                downtime = hermes.get("downtime_seconds", 0)
                msg = f"Hermes is down ({downtime / 60:.0f}m). Tap to wake Termux."
                if DEEPSEEK_API_KEY:
                    insight = deepseek_ask(f"Hermes down {downtime / 60:.0f}m. Status: {hermes.get('status', 'unknown')}. One-sentence actionable suggestion.", 100)
                    if insight:
                        msg += f"\n💡 {insight}"
                send_ntfy("Atlas: Hermes Down", msg, "high")
                last_alert_hermes = now
        else:
            last_alert_hermes = None
        openclaw = check_agent("openclaw")
        log(f"OpenClaw: alive={openclaw.get('alive')}, downtime={openclaw.get('downtime_seconds', 0):.0f}s")
        if not openclaw.get("alive"):
            if not last_alert_openclaw or (now - last_alert_openclaw) > timedelta(minutes=30):
                downtime = openclaw.get("downtime_seconds", 0)
                msg = f"OpenClaw is down ({downtime / 60:.0f}m). Tap to wake."
                if DEEPSEEK_API_KEY:
                    insight = deepseek_ask(f"OpenClaw down {downtime / 60:.0f}m. Status: {openclaw.get('status', 'unknown')}. One-sentence actionable suggestion.", 100)
                    if insight:
                        msg += f"\n💡 {insight}"
                send_ntfy("Atlas: OpenClaw Down", msg, "high")
                last_alert_openclaw = now
        else:
            last_alert_openclaw = None
        if not hermes.get("alive") and not openclaw.get("alive") and not aibrain_ok:
            if not last_alert_termux or (now - last_alert_termux) > timedelta(minutes=30):
                msg = "CRITICAL: Termux is dead. Hermes + OpenClaw + AI Brain all unreachable."
                if DEEPSEEK_API_KEY:
                    insight = deepseek_ask("All swarm agents unreachable. Phone likely off. One-sentence emergency action.", 100)
                    if insight:
                        msg += f"\n🚨 {insight}"
                send_ntfy("Atlas: TERMUX DOWN", msg, "urgent")
                last_alert_termux = now
        else:
            last_alert_termux = None
        insight_counter_local += 1
        if insight_counter_local >= 288:
            log("Running daily tasks...")
            generate_insight()
            calculate_swarm_health()
            decay_field_strengths()
            update_agent_trust("hermes")
            update_agent_trust("openclaw")
            insight_counter_local = 0
        if aibrain_ok:
            aibrain_post("/api/public/v1/memory", {"agent": "atlas", "type": "health_check", "data": {"hermes": hermes.get("alive"), "openclaw": openclaw.get("alive"), "aibrain": aibrain_ok, "time": now.isoformat()}})
        time.sleep(CHECK_INTERVAL)

watcher = threading.Thread(target=watcher_loop, daemon=True)
watcher.start()

@app.route("/")
def health():
    return jsonify({"status": "Atlas alive", "version": "3.0", "time": datetime.utcnow().isoformat(), "monitors": ["hermes", "openclaw"], "check_interval": CHECK_INTERVAL, "ntfy_topic": NTFY_TOPIC, "deepseek": bool(DEEPSEEK_API_KEY), "autonomy_level": ATLAS_AUTONOMY, "collections": ["agent_cards", "agent_trust", "directives", "skill_recommendations", "swarm_health", "constitution", "knowledge_graph", "failure_log", "atlas_insights"]})

@app.route("/status")
def status():
    return jsonify({"atlas": {"alive": True, "time": datetime.utcnow().isoformat(), "autonomy_level": ATLAS_AUTONOMY}, "hermes": check_agent("hermes"), "openclaw": check_agent("openclaw"), "swarm_health": SWARM_HEALTH[-1] if SWARM_HEALTH else None, "latest_insight": ATLAS_INSIGHTS[-1] if ATLAS_INSIGHTS else None})

@app.route("/heartbeat/<agent>", methods=["POST"])
def heartbeat(agent):
    global HEARTBEATS
    data = request.get_json() or {}
    HEARTBEATS[agent] = {"last_seen": datetime.utcnow().isoformat(), "status": data.get("status", "ok"), "payload": data}
    log(f"Heartbeat from {agent}: {data.get('status', 'ok')}")
    aibrain_post("/api/public/v1/memory", {"agent": agent, "type": "heartbeat", "data": data})
    return jsonify({"received": True, "agent": agent, "time": datetime.utcnow().isoformat()})

@app.route("/message", methods=["POST"])
def send_message():
    global MESSAGES, msg_counter
    data = request.get_json() or {}
    sender = data.get("sender", "unknown")
    recipient = data.get("recipient", "")
    payload = data.get("payload", "")
    if not recipient or not payload:
        return jsonify({"error": "recipient and payload required"}), 400
    msg_counter += 1
    msg = {"id": msg_counter, "sender": sender, "recipient": recipient, "payload": payload, "time": datetime.utcnow().isoformat(), "read": False}
    MESSAGES.append(msg)
    log(f"Message #{msg_counter} from {sender} to {recipient}")
    aibrain_post("/api/public/v1/transmissions", {"sender": sender, "recipient": recipient, "payload": payload, "time": msg["time"]})
    return jsonify({"sent": True, "id": msg_counter})

@app.route("/messages/<agent>")
def get_messages(agent):
    global MESSAGES
    unread = [m for m in MESSAGES if m["recipient"] == agent and not m["read"]]
    for m in unread:
        m["read"] = True
    return jsonify({"messages": unread, "count": len(unread)})

@app.route("/wake/<agent>")
def wake_agent(agent):
    send_ntfy(f"Atlas: Wake {agent}", f"Manual wake triggered for {agent}", "default")
    return jsonify({"sent": True, "agent": agent})

@app.route("/intelligence", methods=["POST"])
def intelligence():
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    result = deepseek_ask(prompt, max_tokens=data.get("max_tokens", 300))
    if result:
        return jsonify({"result": result, "model": DEEPSEEK_MODEL})
    return jsonify({"error": "DeepSeek failed"}), 503

@app.route("/directive", methods=["POST"])
def write_directive():
    global DIRECTIVES, directive_counter
    if ATLAS_AUTONOMY < 2:
        return jsonify({"error": f"Blocked: directives require autonomy level 2+. Current: {ATLAS_AUTONOMY}"}), 403
    data = request.get_json() or {}
    if not data.get("agent") or not data.get("task"):
        return jsonify({"error": "agent and task required"}), 400
    if not data.get("success_criteria"):
        return jsonify({"error": "success_criteria required"}), 400
    directive_counter += 1
    directive = {"id": directive_counter, "target_agent": data["agent"], "task": data["task"], "reasoning": data.get("reasoning", ""), "success_criteria": data["success_criteria"], "created_by": "atlas", "source_project": data.get("source_project", ""), "priority": data.get("priority", "medium"), "expires_at": data.get("expires_at"), "status": "open", "created_at": datetime.utcnow().isoformat()}
    DIRECTIVES.append(directive)
    aibrain_post("/api/public/v1/directives", directive)
    log(f"Directive #{directive_counter} for {data['agent']}")
    return jsonify({"directive_written": True, "id": directive_counter})

@app.route("/directives/<agent>")
def get_directives(agent):
    data = aibrain_get(f"/api/public/v1/directives?target_agent={agent}&status=open") or []
    if not data:
        data = [d for d in DIRECTIVES if d["target_agent"] == agent and d["status"] == "open"]
    return jsonify({"directives": data, "count": len(data)})

@app.route("/skill_acquired", methods=["POST"])
def skill_acquired():
    global SKILL_RECS
    data = request.get_json() or {}
    if not data.get("agent_from") or not data.get("agent_to") or not data.get("skill"):
        return jsonify({"error": "agent_from, agent_to, skill required"}), 400
    rec = {"agent_from": data["agent_from"], "agent_to": data["agent_to"], "skill": data["skill"], "reason": data.get("reason", ""), "confidence": data.get("confidence", 0.5), "status": "pending", "created_at": datetime.utcnow().isoformat()}
    SKILL_RECS.append(rec)
    aibrain_post("/api/public/v1/skill_recommendations", rec)
    log(f"Skill: {data['agent_from']} -> {data['agent_to']}: {data['skill']}")
    return jsonify({"recorded": True})

@app.route("/failure", methods=["POST"])
def log_failure():
    global FAILURE_LOG
    data = request.get_json() or {}
    failure = {"agent": data.get("agent", "unknown"), "approach_tried": data.get("approach_tried", ""), "what_failed": data.get("what_failed", ""), "resolution": data.get("resolution", ""), "resolved": data.get("resolved", False), "date": datetime.utcnow().isoformat(), "tags": data.get("tags", [])}
    FAILURE_LOG.append(failure)
    aibrain_post("/api/public/v1/failure_log", failure)
    log(f"Failure for {failure['agent']}: {failure['what_failed'][:50]}...")
    return jsonify({"logged": True})

@app.route("/check_failures")
def check_failures():
    unresolved = [f for f in FAILURE_LOG if not f["resolved"]]
    return jsonify({"failures": unresolved, "count": len(unresolved)})

@app.route("/trust/<agent>")
def get_trust(agent):
    data = aibrain_get(f"/api/public/v1/agent_trust?agent={agent}")
    if not data:
        data = AGENT_TRUST.get(agent)
    if not data:
        return jsonify({"error": "no trust data"}), 404
    return jsonify(data)

@app.route("/insights")
def get_insights():
    data = aibrain_get("/api/public/v1/atlas_insights") or []
    if not data:
        data = ATLAS_INSIGHTS[-10:]
    return jsonify({"insights": data[-10:], "count": len(data)})

@app.route("/knowledge", methods=["POST"])
def add_knowledge():
    global KNOWLEDGE_GRAPH, kg_counter
    data = request.get_json() or {}
    if not data.get("subject") or not data.get("relation") or not data.get("object"):
        return jsonify({"error": "subject, relation, object required"}), 400
    kg_counter += 1
    entry = {"id": kg_counter, "subject": data["subject"], "relation": data["relation"], "object": data["object"], "source": data.get("source", "atlas"), "strength": data.get("strength", 5), "last_reinforced": datetime.utcnow().isoformat()}
    KNOWLEDGE_GRAPH.append(entry)
    aibrain_post("/api/public/v1/knowledge_graph", entry)
    return jsonify({"recorded": True, "id": kg_counter})

@app.route("/knowledge")
def get_knowledge():
    data = aibrain_get("/api/public/v1/knowledge_graph") or []
    if not data:
        data = KNOWLEDGE_GRAPH
    sorted_kg = sorted(data, key=lambda x: (-x.get("strength", 0), x.get("last_reinforced", "")))
    return jsonify({"knowledge": sorted_kg[:20], "total": len(sorted_kg)})

@app.route("/constitution")
def get_constitution():
    data = aibrain_get("/api/public/v1/constitution") or []
    if not data:
        data = CONSTITUTION
    return jsonify({"rules": data, "count": len(data)})

@app.route("/swarm_health")
def get_swarm_health():
    data = aibrain_get("/api/public/v1/swarm_health") or []
    if not data:
        data = SWARM_HEALTH
    return jsonify({"history": data[-7:], "latest": data[-1] if data else None})

@app.route("/card/<agent>", methods=["POST"])
def update_card(agent):
    data = request.get_json() or {}
    card = {"agent": agent, "capabilities": data.get("capabilities", []), "current_task": data.get("current_task", ""), "last_reasoning_trace": data.get("reasoning_trace", ""), "updated_at": datetime.utcnow().isoformat()}
    AGENT_CARDS[agent] = card
    aibrain_post("/api/public/v1/agent_cards", card)
    log(f"Card updated for {agent}")
    return jsonify({"recorded": True})

@app.route("/card/<agent>")
def get_card(agent):
    data = aibrain_get(f"/api/public/v1/agent_cards?agent={agent}")
    if not data:
        data = AGENT_CARDS.get(agent)
    if not data:
        return jsonify({"error": "no card found"}), 404
    return jsonify(data)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

VERIFICATIONS = {}

@app.route("/verify-task", methods=["POST"])
def verify_task():
    data = request.get_json(force=True)
    agent = data.get("agent")
    stage = data.get("stage")
    task = data.get("task")

    if stage == "start":
        VERIFICATIONS[agent] = {"task": task, "report": None, "verdict": None}
        return jsonify({"status": "snapshot recorded"})

    if stage == "report":
        report = data.get("report", "")
        original_task = VERIFICATIONS.get(agent, {}).get("task", task)
        prompt = f"Task given: {original_task}\nAgent's claimed report: {report}\nDid the agent actually complete the task as instructed? Return ONLY JSON: {{\"verdict\": \"pass\" or \"fail\", \"reason\": string}}"
        result = deepseek_ask(prompt, 200)
        try:
            verdict = json.loads(result) if result else {"verdict": "unknown", "reason": "no judge response"}
        except Exception:
            verdict = {"verdict": "unknown", "reason": "unparseable judge response"}
        if agent in VERIFICATIONS:
            VERIFICATIONS[agent]["report"] = report
            VERIFICATIONS[agent]["verdict"] = verdict
        return jsonify(verdict)

    return jsonify({"error": "unknown stage"}), 400
