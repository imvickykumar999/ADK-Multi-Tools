# --------------------------------------------------------------
# app.py - Full Zoom Meeting Scheduler API
# --------------------------------------------------------------
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime
import pytz, requests, json, os, time, base64
from urllib.parse import urlencode
from dotenv import load_dotenv

# ----------------------------------------------------------------
# 1. Load environment variables
# ----------------------------------------------------------------
load_dotenv()

app = Flask(__name__)

# ----------------------------------------------------------------
# 2. Rate limiting
# ----------------------------------------------------------------
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["10 per minute"]
)

# ----------------------------------------------------------------
# 3. Config
# ----------------------------------------------------------------
TOKEN_FILE = "zoom_token.json"
CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:5000/oauth/callback"

if not CLIENT_ID or not CLIENT_SECRET:
    raise RuntimeError("ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET must be in .env")

# ----------------------------------------------------------------
# 4. Token helpers
# ----------------------------------------------------------------
def load_token():
    if not os.path.exists(TOKEN_FILE):
        raise FileNotFoundError("zoom_token.json not found")
    with open(TOKEN_FILE) as f:
        token = json.load(f)
    if "expires_at" not in token:
        token["expires_at"] = time.time() + token.get("expires_in", 3600)
        save_token(token)
    return token

def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)

def token_expired(token):
    return time.time() > (token.get("expires_at", 0) - 120)

def refresh_access_token(token):
    print("Refreshing Zoom token...")
    url = "https://zoom.us/oauth/token"
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    data = {
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"]
    }
    headers = {"Authorization": f"Basic {auth}"}
    r = requests.post(url, headers=headers, data=data)
    if r.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {r.text}")
    new = r.json()
    new["expires_at"] = time.time() + new.get("expires_in", 3600)
    save_token(new)
    return new

# ----------------------------------------------------------------
# 5. OAuth: Generate zoom_token.json on first use
# ----------------------------------------------------------------
@app.route("/oauth/login")
def oauth_login():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI
    }
    url = f"https://zoom.us/oauth/authorize?{urlencode(params)}"
    return f'''
    <h2>Zoom OAuth Required</h2>
    <p><strong>zoom_token.json</strong> not found. Please authorize the app:</p>
    <a href="{url}" style="padding:10px 20px; background:#0B5CFF; color:white; text-decoration:none; border-radius:5px;">
        Login with Zoom
    </a>
    <p><small>After approval, return here and refresh.</small></p>
    '''

@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    if not code:
        return "Error: Authorization denied or code missing.", 400

    token_url = "https://zoom.us/oauth/token"
    auth = (CLIENT_ID, CLIENT_SECRET)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    r = requests.post(token_url, auth=auth, data=data)
    if r.status_code != 200:
        return f"Token exchange failed: {r.text}", 500

    token_data = r.json()
    token_data["expires_at"] = time.time() + token_data.get("expires_in", 3600)
    save_token(token_data)

    return '''
    <h2>Success!</h2>
    <p><strong>zoom_token.json</strong> created successfully.</p>
    <p>You can now use the API:</p>
    <pre>curl -X POST http://localhost:5000/api/schedule/ -H "Content-Type: application/json" -d '{"topic":"Test","start_time":"2025-11-15T10:00:00","duration":30,"timezone":"Asia/Kolkata"}'</pre>
    <p><a href="/">← Back</a></p>
    '''

# ----------------------------------------------------------------
# 6. API: Schedule Meeting (Dynamic)
# ----------------------------------------------------------------
@app.route("/api/schedule/", methods=["POST"])
@limiter.limit("5 per minute")
def schedule_meeting():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "JSON body required"}), 400

        # Required fields
        required = ["topic", "start_time", "duration", "timezone"]
        for field in required:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing: {field}"}), 400

        topic = data["topic"]
        duration = int(data["duration"])
        start_time_str = data["start_time"]
        timezone_str = data["timezone"]

        # Validate timezone
        if timezone_str not in pytz.all_timezones:
            return jsonify({"success": False, "error": f"Invalid timezone: {timezone_str}"}), 400

        # Parse and convert start_time to UTC
        try:
            dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                tz = pytz.timezone(timezone_str)
                dt = tz.localize(dt)
            utc_dt = dt.astimezone(pytz.UTC)
            iso_start = utc_dt.isoformat(timespec='seconds').replace("+00:00", "Z")
        except Exception as e:
            return jsonify({"success": False, "error": f"Invalid start_time: {str(e)}"}), 400

        # Load & refresh token
        try:
            token = load_token()
            if token_expired(token):
                token = refresh_access_token(token)
        except FileNotFoundError:
            return jsonify({
                "success": False,
                "error": "zoom_token.json not found",
                "setup_url": "http://localhost:5000/oauth/login"
            }), 401

        # Create meeting
        url = "https://api.zoom.us/v2/users/me/meetings"
        headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "Content-Type": "application/json"
        }
        payload = {
            "topic": topic,
            "type": 2,
            "start_time": iso_start,
            "duration": duration,
            "timezone": timezone_str,
            "settings": {
                "join_before_host": data.get("join_before_host", True),
                "mute_upon_entry": data.get("mute_upon_entry", True),
                "waiting_room": data.get("waiting_room", False)
            }
        }

        r = requests.post(url, headers=headers, json=payload)
        if r.status_code == 429:
            retry = int(r.headers.get("Retry-After", 60))
            return jsonify({"error": "Rate limited", "retry_after": retry}), 429

        if r.status_code != 201:
            try:
                err = r.json()
            except:
                err = r.text
            return jsonify({"success": False, "error": str(err)}), r.status_code

        meeting = r.json()

        return jsonify({
            "success": True,
            "meeting": {
                "id": meeting.get("id"),
                "topic": meeting.get("topic"),
                "join_url": meeting.get("join_url"),
                "start_url": meeting.get("start_url"),
                "password": meeting.get("password"),
                "start_time": meeting.get("start_time"),
                "duration": meeting.get("duration"),
                "timezone": meeting.get("timezone"),
                "created_at": meeting.get("created_at")
            }
        }), 201

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ----------------------------------------------------------------
# 7. Health check
# ----------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

# ----------------------------------------------------------------
# 8. Home (optional)
# ----------------------------------------------------------------
@app.route("/")
def home():
    return '''
    <h1>Zoom Scheduler API</h1>
    <p><a href="/api/schedule/" target="_blank">POST /api/schedule/</a> → Create meeting</p>
    <p><a href="/health">/health</a> → Check status</p>
    <p><a href="/oauth/login">/oauth/login</a> → Generate zoom_token.json (first time)</p>
    '''

# ----------------------------------------------------------------
# 9. Run
# ----------------------------------------------------------------
if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    port = int(os.getenv("PORT", 8000))
    print(f"Server running at http://localhost:{port}")
    print(f"   • API: POST /api/schedule/")
    print(f"   • OAuth: /oauth/login")
    app.run(host="0.0.0.0", port=port, debug=debug)
