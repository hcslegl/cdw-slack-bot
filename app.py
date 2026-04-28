import os
import hmac
import hashlib
import time
import threading
import requests
from flask import Flask, request, jsonify
from scraper import get_order_info

app = Flask(__name__)

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")


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
    except Exception as e:
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


@app.route("/health", methods=["GET"])
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
