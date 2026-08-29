#!/usr/bin/env python3
"""
Atlas — MA'AT Swarm Independent Verifier
Cloud-side: receives goal data from local relay, judges via Groq,
writes verdicts to SwarmHive OS and Supabase.
Neo4j is never touched here — relay script owns all local writes.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("atlas")

# ── Environment ────────────────────────────────────────────────
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
SWARMHIVE_URL     = os.getenv("SWARMHIVE_URL",
                       "https://command-glass.lovable.app/api/public/v1/os")
SWARMHIVE_KEY     = os.getenv("SWARMHIVE_KEY", "")
SUPABASE_URL      = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY", "")
NTFY_TOPIC        = os.getenv("NTFY_TOPIC", "")
PORT              = int(os.getenv("PORT", 10000))

# ── Groq judge ─────────────────────────────────────────────────
def groq_ask(prompt, max_tokens=400):
    if not GROQ_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": GROQ_MODEL,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens},
            timeout=60
        )
        if r.status_code == 200:
            msg = r.json()["choices"][0]["message"]
            return (msg.get("content") or msg.get("reasoning") or "").strip()
        log.error(f"Groq failed: HTTP {r.status_code} {r.text}")
        return None
    except Exception as e:
        log.error(f"Groq exception: {e}")
        return None

# ── Supabase logger ────────────────────────────────────────────
def supabase_log(record: dict):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/atlas_goals",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates"
            },
            json=record,
            timeout=10
        )
    except Exception as e:
        log.error(f"Supabase log failed: {e}")

# ── SwarmHive verdict writer ───────────────────────────────────
def swarmhive_verdict(goal_id, result, rating, notes):
    if not SWARMHIVE_KEY:
        return
    try:
        r = requests.post(
            f"{SWARMHIVE_URL}/verify",
            headers={"Authorization": f"Bearer {SWARMHIVE_KEY}",
                     "Content-Type": "application/json"},
            json={"actor": "atlas", "goal_id": goal_id,
                  "result": result, "rating": rating, "notes": notes},
            timeout=15
        )
        if r.status_code == 200:
            log.info(f"SwarmHive verdict posted: {result}")
        else:
            log.error(f"SwarmHive verdict failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log.error(f"SwarmHive verdict exception: {e}")

# ── NTFY notifier ──────────────────────────────────────────────
def ntfy_notify(message):
    if not NTFY_TOPIC:
        return
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode(), timeout=5)
    except Exception:
        pass

# ── In-memory goal store (survives within one Render instance) ─
GOALS = {}

# ── Routes ─────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "agent": "atlas",
                    "goals_in_memory": len(GOALS)})

@app.route("/goal", methods=["POST"])
def receive_goal():
    """Receive snapshot from relay. Store and log to Supabase."""
    data = request.get_json(force=True) or {}
    goal_id  = data.get("goal_id")
    agent    = data.get("agent", "unknown")
    title    = data.get("title", "")
    dod      = data.get("definition_of_done", "")
    snap_at  = data.get("snapshot_at",
                   datetime.now(timezone.utc).isoformat())

    if not goal_id:
        return jsonify({"error": "goal_id required"}), 400

    GOALS[goal_id] = {
        "goal_id": goal_id, "agent": agent,
        "instruction": title, "definition_of_done": dod,
        "snapshot_at": snap_at, "report": None,
        "verdict": None
    }

    supabase_log({
        "goal_id": goal_id, "agent": agent,
        "instruction": title, "definition_of_done": dod,
        "snapshot_at": snap_at
    })

    log.info(f"Goal received: {goal_id} agent={agent}")
    return jsonify({"status": "snapshot_recorded", "goal_id": goal_id})

@app.route("/report", methods=["POST"])
def receive_report():
    """Receive agent report, run Groq judge, return verdict."""
    data = request.get_json(force=True) or {}
    goal_id     = data.get("goal_id")
    report_text = data.get("report", "")
    status      = data.get("status", "completed")
    reported_at = data.get("reported_at",
                      datetime.now(timezone.utc).isoformat())

    if not goal_id:
        return jsonify({"error": "goal_id required"}), 400

    goal = GOALS.get(goal_id, {})
    original_task = goal.get("instruction", "")
    dod           = goal.get("definition_of_done", "")

    prompt = (
        f"Task given: {original_task}\n"
        f"Definition of done: {dod}\n"
        f"Agent\'s claimed report: {report_text}\n"
        f"Did the agent actually complete the task as instructed?\n"
        f"Return ONLY JSON: "
        f"{{\"verdict\": \"pass\" or \"partial\" or \"fail\", "
        f"\"score\": 0-10, \"reason\": string}}"
    )

    raw = groq_ask(prompt, 400)
    try:
        verdict = json.loads(raw) if raw else {
            "verdict": "unknown", "reason": "no judge response", "score": None}
    except Exception:
        verdict = {"verdict": "unknown",
                   "reason": "unparseable judge response", "score": None}

    result  = verdict.get("verdict", "unable_to_verify")
    rating  = verdict.get("score")
    notes   = verdict.get("reason", "")

    # Update memory
    if goal_id in GOALS:
        GOALS[goal_id].update({
            "report": report_text, "verdict": verdict,
            "reported_at": reported_at
        })

    # Write to Supabase
    supabase_log({
        "goal_id": goal_id,
        "report": report_text, "report_status": status,
        "verdict_result": result,
        "verdict_rating": rating,
        "verdict_notes": notes,
        "reported_at": reported_at,
        "verified_at": datetime.now(timezone.utc).isoformat() + "Z"
    })

    # Write verdict to SwarmHive
    swarmhive_verdict(goal_id, result, rating, notes)

    # Notify
    ntfy_notify(
        f"Atlas verdict: {result.upper()} | {goal.get('agent','?')} | "
        f"{original_task[:60]}"
    )

    log.info(f"Verdict: {goal_id} result={result} score={rating}")
    return jsonify(verdict)

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    """Receive agent heartbeat. Log presence."""
    data  = request.get_json(force=True) or {}
    agent = data.get("agent", "unknown")
    state = data.get("state", "unknown")
    log.info(f"Heartbeat: agent={agent} state={state}")
    return jsonify({"status": "received", "agent": agent, "state": state})

@app.route("/status")
def status():
    """Return current in-memory goal count and recent goals."""
    recent = list(GOALS.values())[-5:]
    return jsonify({"active_goals": len(GOALS), "recent": recent})

# Legacy endpoint — kept for backward compatibility with relay
@app.route("/verify-task", methods=["POST"])
def verify_task_legacy():
    """Legacy relay endpoint. Maps to new /goal or /report."""
    data  = request.get_json(force=True) or {}
    stage = data.get("stage", "")
    if stage == "start":
        # Remap to /goal logic inline
        agent   = data.get("agent", "unknown")
        task    = data.get("task", "")
        fake_id = f"legacy-{agent}-{datetime.now(timezone.utc).timestamp()}"
        GOALS[fake_id] = {"goal_id": fake_id, "agent": agent,
                          "instruction": task, "report": None, "verdict": None}
        return jsonify({"status": "snapshot recorded"})
    elif stage == "report":
        agent       = data.get("agent", "unknown")
        report_text = data.get("report", "")
        task        = data.get("task", "")
        prompt = (
            f"Task given: {task}\n"
            f"Agent\'s claimed report: {report_text}\n"
            f"Did the agent complete the task?\n"
            f"Return ONLY JSON: "
            f"{{\"verdict\": \"pass\" or \"fail\", "
            f"\"score\": 0-10, \"reason\": string}}"
        )
        raw = groq_ask(prompt, 400)
        try:
            verdict = json.loads(raw) if raw else {
                "verdict": "unknown", "reason": "no judge response"}
        except Exception:
            verdict = {"verdict": "unknown",
                       "reason": "unparseable judge response"}
        return jsonify(verdict)
    return jsonify({"error": "unknown stage"}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
