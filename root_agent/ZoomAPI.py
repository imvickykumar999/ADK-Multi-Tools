# root_agent/Bol7API.py
import json
import requests
from typing import Dict, Any, Optional
from datetime import datetime
import pytz


def schedule_meeting(
    topic: str,
    start_time: str,       # ISO local time: "2025-11-15T14:30:00"
    duration: int,         # minutes
    timezone: str,         # IANA: "Asia/Kolkata"
    join_before_host: bool = True,
    mute_upon_entry: bool = True,
    waiting_room: bool = False,
) -> Dict[str, Any]:
    """
    Schedule a Zoom meeting using your local API.

    Matches exactly what your Flask API expects:
    POST http://localhost:5000/api/schedule/

    Args:
        topic: Meeting title
        start_time: ISO format (local time, no Z or offset)
        duration: Length in minutes
        timezone: IANA timezone string
        join_before_host: Allow early join
        mute_upon_entry: Mute on entry
        waiting_room: Enable waiting room

    Returns:
        Dict with 'content', 'artifact', 'is_error'
    """
    # --- 1. Validate required fields ---
    missing = []
    if not topic: missing.append("topic")
    if not start_time: missing.append("start_time")
    if duration is None or duration <= 0: missing.append("duration")
    if not timezone: missing.append("timezone")

    if missing:
        return {
            "content": (
                "Please provide the missing details: **{}**.\n"
                "Example: `topic: Team Sync`, `start_time: 2025-11-16T14:30:00`, etc."
            ).format(", ".join(missing)),
            "is_error": False,
        }

    # --- 2. Validate & convert start_time to UTC ---
    try:
        dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            tz = pytz.timezone(timezone)
            dt = tz.localize(dt)
        utc_start = dt.astimezone(pytz.UTC).isoformat(timespec='seconds').replace("+00:00", "Z")
    except Exception as e:
        return {
            "content": f"Invalid start_time format. Use: `YYYY-MM-DDTHH:MM:SS` → {str(e)}",
            "is_error": True,
        }

    # --- 3. Validate timezone ---
    if timezone not in pytz.all_timezones:
        return {
            "content": f"Invalid timezone: `{timezone}`. Use IANA name like `Asia/Kolkata`, `America/New_York`.",
            "is_error": True,
        }

    # --- 4. Build payload for your Flask API ---
    url = "http://localhost:8000/api/schedule/"
    payload = {
        "topic": topic,
        "start_time": utc_start,
        "duration": duration,
        "timezone": timezone,
        "join_before_host": join_before_host,
        "mute_upon_entry": mute_upon_entry,
        "waiting_room": waiting_room,
    }
    headers = {"Content-Type": "application/json"}

    # --- 5. Call API ---
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()

        if not result.get("success"):
            return {
                "content": f"Zoom API error: {result.get('error', 'Unknown')}",
                "is_error": True,
            }

        meeting = result["meeting"]

        # --- 6. Format human-readable success ---
        local_time = datetime.fromisoformat(meeting["start_time"].replace("Z", "+00:00"))
        local_time = local_time.astimezone(pytz.timezone(timezone))
        pretty_time = local_time.strftime("%B %d, %Y at %I:%M %p")

        content = (
            f"**Meeting Scheduled Successfully!**\n\n"
            f"**Topic:** {meeting['topic']}\n"
            f"**When:** {pretty_time} ({timezone})\n"
            f"**Duration:** {duration} minutes\n"
            f"**Join Link:** {meeting['join_url']}\n"
            f"**Meeting ID:** `{meeting['id']}`\n"
            f"**Passcode:** `{meeting['password']}`\n\n"
            f"_Host start link (private): {meeting['start_url']}_"
        )

        return {
            "content": content,
            "artifact": result,
            "is_error": False,
        }

    except requests.exceptions.HTTPError as http_err:
        return {
            "content": f"API call failed (HTTP {resp.status_code}): {resp.text}",
            "is_error": True,
        }
    except Exception as exc:
        return {
            "content": f"Unexpected error: {str(exc)}",
            "is_error": True,
        }
