import os
import hmac
import hashlib
import time
import json
import threading
import requests
from flask import Flask, request, jsonify
from scraper import get_order_info
from session import set_cookies, get_cookies

app = Flask(__name__)

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")


def verify_slack_signature(req):
    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    try:
        if abs(time.time() - float(timestamp)) > 300:
            return False
    except ValueError:
        return False

    sig_basestring = f"v0:{timestamp}:{req.get_data(as_text=True)}"
    expected = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()
    slack_sig = req.headers.get("X-Slack-Signature", "")
    return hmac.compare_digest(expected, slack_sig)


def process_and_respond(name, response_url):
    try:
        result = get_order_info(name)
        payload = {"text": result, "response_type": "in_channel"}
    except RuntimeError as e:
        print(f"[ERROR] RuntimeError for '{name}': {e}", flush=True)
        if "CDW login failed" in str(e):
            payload = {"text": ":warning: The CDW order lookup is temporarily unavailable. Please contact *Henry Slegl* or the *IT Help Desk* for assistance."}
        else:
            payload = {"text": f":warning: Error looking up orders for *{name}*: {e}"}
    except Exception as e:
        print(f"[ERROR] Exception for '{name}': {e}", flush=True)
        payload = {"text": f":warning: Error looking up orders for *{name}*: {e}"}
    requests.post(response_url, json=payload)


@app.route("/trackorder", methods=["POST"])
def track_order():
    if not verify_slack_signature(request):
        return jsonify({"error": "Invalid signature"}), 403

    name = request.form.get("text", "").strip()
    response_url = request.form.get("response_url")

    if not name:
        return jsonify({"text": "Usage: `/trackorder First Last`"})

    thread = threading.Thread(target=process_and_respond, args=(name, response_url))
    thread.daemon = True
    thread.start()

    return jsonify({"text": f":mag: Looking up orders for *{name}*..."})


def open_refresh_modal(trigger_id):
    modal = {
        "trigger_id": trigger_id,
        "view": {
            "type": "modal",
            "callback_id": "refresh_session_modal",
            "title": {"type": "plain_text", "text": "Refresh CDW Session"},
            "submit": {"type": "plain_text", "text": "Update Cookies"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*Steps to refresh your session:*\n"
                            "1. Log into CDW in Chrome\n"
                            "2. Click the *Cookie-Editor* extension\n"
                            "3. Click *Export* → *Export as JSON*\n"
                            "4. Paste the copied JSON below"
                        ),
                    },
                },
                {
                    "type": "input",
                    "block_id": "cookies_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "cookies_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Paste cookie JSON here...",
                        },
                    },
                    "label": {"type": "plain_text", "text": "Cookie JSON"},
                },
            ],
        },
    }
    requests.post(
        "https://slack.com/api/views.open",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json=modal,
    )


@app.route("/refreshsession", methods=["POST"])
def refresh_session():
    if not verify_slack_signature(request):
        return jsonify({"error": "Invalid signature"}), 403

    trigger_id = request.form.get("trigger_id")

    thread = threading.Thread(target=open_refresh_modal, args=(trigger_id,))
    thread.daemon = True
    thread.start()

    return jsonify({}), 200


@app.route("/slack/interactions", methods=["POST"])
def slack_interactions():
    if not verify_slack_signature(request):
        return jsonify({"error": "Invalid signature"}), 403

    payload = json.loads(request.form.get("payload", "{}"))

    if payload.get("type") == "view_submission" and payload.get("view", {}).get("callback_id") == "refresh_session_modal":
        cookies_json = (
            payload["view"]["state"]["values"]["cookies_block"]["cookies_input"]["value"] or ""
        ).strip()

        try:
            set_cookies(cookies_json)
        except ValueError as e:
            return jsonify({
                "response_action": "errors",
                "errors": {"cookies_block": str(e)},
            })

        # Notify the user in Slack
        user_id = payload.get("user", {}).get("id", "")
        if user_id and SLACK_BOT_TOKEN:
            requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                json={
                    "channel": user_id,
                    "text": ":white_check_mark: CDW session cookies updated successfully!",
                },
            )

        return jsonify({"response_action": "clear"})

    return jsonify({}), 200


@app.route("/internal/refresh-cookies", methods=["POST"])
def internal_refresh_cookies():
    secret = os.environ.get("REFRESH_SECRET", "")
    auth = request.headers.get("Authorization", "")
    if not secret or not hmac.compare_digest(auth, f"Bearer {secret}"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    cookies = data.get("cookies", [])
    if not cookies:
        return jsonify({"error": "No cookies provided"}), 400

    try:
        set_cookies(json.dumps(cookies))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"ok": True, "count": len(cookies)})


@app.route("/health", methods=["GET"])
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
