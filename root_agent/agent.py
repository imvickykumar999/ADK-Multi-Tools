from google.adk.agents.llm_agent import Agent
from .ZoomAPI import schedule_meeting
from dateutil.parser import parse
from dateutil.tz import gettz

def convert_to_iso(datetime_string: str, timezone_iana: str) -> str:
    """
    Converts a human-readable date and time string into a full ISO 8601 format string
    (e.g., '2025-11-16T14:30:00').

    The agent should call this tool with the user's natural language input
    (e.g., 'tomorrow at 3 PM') and the collected IANA timezone (e.g., 'America/Los_Angeles').

    Args:
        datetime_string: The human-readable date and time (e.g., 'tomorrow at 3 PM').
        timezone_iana: The IANA timezone string (e.g., 'Asia/Kolkata', 'America/New_York').
                       This is REQUIRED for accurate conversion.

    Returns:
        The localized datetime string in ISO 8601 format (YYYY-MM-DDTHH:MM:SS).
    """
    try:
        # 1. Determine the target timezone
        tz = gettz(timezone_iana)
        if not tz:
            return f"Error: The timezone '{timezone_iana}' is not valid."

        # 2. Parse the human-readable string.
        # This gives a naive (no timezone) datetime object based on the current system's time.
        dt_obj = parse(datetime_string)

        # 3. Localize the naive object to the specified timezone.
        # This correctly interprets '3 PM' as 3 PM in the user's specified timezone.
        dt_localized = dt_obj.replace(tzinfo=tz)

        # 4. Format to the required ISO string (YYYY-MM-DDTHH:MM:SS)
        # We explicitly omit the timezone offset from the string output.
        return dt_localized.strftime("%Y-%m-%dT%H:%M:%S")

    except Exception as e:
        # This error handling provides clear guidance to the agent/user if parsing fails
        return f"Error: Could not parse '{datetime_string}' using timezone '{timezone_iana}'. Please specify a clearer date/time."

root_agent = Agent(
    model="gemini-2.5-flash",
    name="root_agent",
    description="A friendly meeting scheduler that collects all details before booking. Capable of converting natural language time input (like 'tomorrow at 3 PM') into ISO format for scheduling.",
    instruction = """
You are a friendly scheduler dedicated to booking Zoom meetings. Your process requires **three** mandatory inputs.

**CRITICAL DEFAULT:** The **Timezone** is automatically set to **'Asia/Kolkata'**. You **do not** need to ask the user for this.

**Data Collection Order (Interactive):**
1. Meeting **topic** (e.g., Q3 Strategy Review)
2. **Start time** (in natural language, e.g., 'next Tuesday at 10:00 AM')
3. **Duration** in minutes (as an integer)

**Tool Usage Protocol (Critical Steps):**

1.  **Tool 1: `convert_to_iso` (Internal Conversion)**
    * **When to Use:** Immediately after collecting the **Start time** (natural language). Use the default timezone **'Asia/Kolkata'**.
    * **Purpose:** To transform the human-readable time into the strict ISO 8601 format required by the final booking function.
    * **Example Call:** `convert_to_iso(datetime_string="next Tuesday at 10:00 AM", timezone_iana="Asia/Kolkata")`
    * **Result:** You will receive the ISO time string (e.g., '2025-11-19T10:00:00'). Store this result.

2.  **Tool 2: `schedule_meeting` (Final Action)**
    * **When to Use:** ONLY when you have collected all three mandatory fields, and the **Start time has been successfully converted to ISO format** using Tool 1.
    * **Purpose:** To finalize the meeting booking.
    * **Example Call:** `schedule_meeting(topic="Q3 Strategy Review", start_time="2025-11-19T10:00:00", timezone="Asia/Kolkata", duration_minutes=60)`

Optional: You may ask for Name, Email, and if they want join-before-host at any point.

After the successful `schedule_meeting` call, show the response in bullet points.
""".strip(),
    tools=[
        schedule_meeting, 
        convert_to_iso
    ],
)
