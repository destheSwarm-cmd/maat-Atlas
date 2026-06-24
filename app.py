import os
import time
import threading
import requests
from datetime import datetime, timedelta
from flask import Flask, jsonify

app = Flask(__name__)

AI_BRAIN_URL = os.getenv("AI_BRAIN_URL", "").rstrip("/")
AI_BRAIN_API_KEY = os.getenv("AI_BRAIN_API_KEY", "")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "maat-atlas-wake")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

last_alert_hermes = None
last_alert_openclaw = None
last_alert_aibrain = None

def log(msg):
    print(f"[{datetime.utcnow().isoformat()}] {msg}")

def check_agent(agent_name):
    try:
        headers = {}
        if AI_BRAIN_API_KEY:
            headers["Authorization"] = f"Bearer {AI_BRAIN_API_KEY}"
        url = f"{AI_BRAIN_URL}/health?agent={agent_name}"
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        last_beat_str = data.get("last_heartbeat") or data.get("timestamp")
        if last_beat_str:
            last_beat = datetime.fromisoformat(last_beat_str.replace("Z", "+00:00"))
            downtime = datetime.now().astimezone() - last_beat
            return {"alive": downtime < timedelta(minutes=10), "last_heartbeat": last_beat_str, "downtime_seconds": downtime.total_seconds()}
        return {"alive": False, "error": "no heartbeat field"}
    except Exception as e:
        return {"alive": False, "error": str(e)}

def send_ntfy(title, message, priority="high"):
    try:
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        headers = {"Title": title, "Priority": priority, "Tags": "warning"}
        r = requests.post(url, data=message, headers=headers, timeout=10)
        log(f"ntfy sent: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        log(f"ntfy failed: {e}")
        return False

def watcher_loop():
    global last_alert_hermes, last_alert_openclaw, last_alert_aibrain
    log("Atlas watcher started")
    while True:
        log("Running health checks...")
        aibrain_ok = False
        try:
            r = requests.get(f"{AI_BRAIN_URL}/health", timeout=10)
            aibrain_ok = r.status_code == 200
            if not aibrain_ok and (not last_alert_aibrain or datetime.now() - last_alert_aibrain > timedelta(minutes=30)):
                send_ntfy("Atlas Alert", "AI Brain is unreachable. All monitoring blind.", "urgent")
                last_alert_aibrain = datetime.now()
        except Exception as e:
            log(f"AI Brain check failed: {e}")
            if not last_alert_aibrain or datetime.now() - last_alert_aibrain > timedelta(minutes=30)):
                send_ntfy("Atlas Alert", f"AI Brain unreachable: {e}", "urgent")
                last_alert_aibrain = datetime.now()
        if aibrain_ok:
            last_alert_aibrain = None
            hermes = check_agent("hermes")
            log(f"Hermes: alive={hermes.get('alive')}, downtime={hermes.get('downtime_seconds', 0):.0f}s")
            if not hermes.get("alive"):
                if not last_alert_hermes or datetime.now() - last_alert_hermes > timedelta(minutes=30)):
                    downtime = hermes.get('downtime_seconds', 0)
                    msg = f"Hermes is down ({downtime/60:.0f}m). Tap to wake Termux."
                    send_ntfy("Atlas: Hermes Down", msg, "high")
                    last_alert_hermes = datetime.now()
            else:
                last_alert_hermes = None
            openclaw = check_agent("openclaw")
            log(f"OpenClaw: alive={openclaw.get('alive')}, downtime={openclaw.get('downtime_seconds', 0):.0f}s")
            if not openclaw.get("alive"):
                if not last_alert_openclaw or datetime.now() - last_alert_openclaw > timedelta(minutes=30)):
                    downtime = openclaw.get('downtime_seconds', 0)
                    msg = f"OpenClaw is down ({downtime/60:.0f}m). Tap to wake."
                    send_ntfy("Atlas: OpenClaw Down", msg, "high")
                    last_alert_openclaw = datetime.now()
            else:
                last_alert_openclaw = None
        time.sleep(CHECK_INTERVAL)

watcher = threading.Thread(target=watcher_loop, daemon=True)
watcher.start()

@app.route('/')
def health():
    return jsonify({"status": "Atlas alive", "time": datetime.utcnow().isoformat(), "monitors": ["hermes", "openclaw"], "check_interval": CHECK_INTERVAL, "ntfy_topic": NTFY_TOPIC})

@app.route('/status')
def status():
    return jsonify({"atlas": {"alive": True, "time": datetime.utcnow().isoformat()}, "hermes": check_agent("hermes"), "openclaw": check_agent("openclaw")})

@app.route('/wake/<agent>')
def wake_agent(agent):
    msg = f"Manual wake triggered for {agent}"
    send_ntfy(f"Atlas: Wake {agent}", msg, "default")
    return jsonify({"sent": True, "agent": agent})

if __name__ == '__main__':
    port = int(os.getenv("PORT", "10000"))
    app.run(host='0.0.0.0', port=port)