import os
import asyncio
import sqlite3
import secrets
from dotenv import load_dotenv
from flask import Flask, request, jsonify, g, redirect, url_for, make_response, Response, render_template
# MODIFIED: Use DatabaseSessionService for persistent sessions
from google.adk.sessions import DatabaseSessionService 
from google.adk.runners import Runner
from google.genai.types import Content, Part

# NOTE: The 'instance.agent' module is assumed to be available in the execution environment.
# Ensure 'instance/agent.py' exists and exports a 'root_agent' instance for this to work.
try:
    # If using ADK, this is where your agent definition lives
    from root_agent.agent import root_agent
except ImportError:
    print("WARNING: 'instance.agent' could not be imported. Agent functionality will be disabled.")
    root_agent = None

# Load environment variables from .env file
load_dotenv()

# --- ADK Initialization & Global State ---
APP_NAME = "agent_flask"
USER_ID = "web_user" # Keeping a fixed user ID for this web demo

# --- Database Configuration ---
DATABASE = 'history.db'
DB_URL = os.getenv("SESSION_DB_URL", f"sqlite:///./{DATABASE}")

# Initialize Flask App
# Flask will look for templates in a 'templates' folder automatically
app = Flask(__name__)

# --- Database Functions ---

def get_db():
    """Returns a database connection, creating one if not present in flask.g."""
    if 'db' not in g:
        # This function connects to history.db
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row  # Allows accessing columns by name
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    """Closes the database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """Initializes the database and creates the messages table."""
    with app.app_context():
        db = get_db()
        # Create a table to store chat messages (for UI history display)
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL, -- 'user' or 'agent'
                text TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()

def save_message(session_id: str, role: str, text: str):
    """Saves a single message to the database."""
    try:
        db = get_db()
        db.execute(
            "INSERT INTO messages (session_id, role, text) VALUES (?, ?, ?)",
            (session_id, role, text)
        )
        db.commit()
    except Exception as e:
        app.logger.error(f"Database Save Error: {e}")

def load_history(session_id: str) -> list[dict]:
    """Loads all messages for a given session ID."""
    try:
        db = get_db()
        rows = db.execute(
            "SELECT role, text FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,)
        ).fetchall()
        return [{"role": row['role'], "text": row['text']} for row in rows]
    except Exception as e:
        app.logger.error(f"Database Load Error: {e}")
        return []

def get_all_session_ids() -> list[str]:
    """Loads all unique session IDs from the database."""
    try:
        db = get_db()
        rows = db.execute(
            # Order by timestamp of the last message in that session for better display logic
            """
            SELECT T1.session_id FROM messages T1
            INNER JOIN (
                SELECT session_id, MAX(timestamp) AS max_timestamp
                FROM messages
                GROUP BY session_id
            ) T2 ON T1.session_id = T2.session_id AND T1.timestamp = T2.max_timestamp
            GROUP BY T1.session_id
            ORDER BY T2.max_timestamp DESC
            """
        ).fetchall()
        return [row['session_id'] for row in rows]
    except Exception as e:
        app.logger.error(f"Database Session Load Error: {e}")
        return []


# Initialize DatabaseSessionService using the consolidated DB_URL
session_service = DatabaseSessionService(db_url=DB_URL)

# Create the runner with the agent only if root_agent was successfully imported
runner = None
adk_sessions = {} # Dictionary to track which sessions have been accessed since restart

if root_agent:
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    async def initialize_adk_session(session_id: str):
        """
        Ensures the ADK session is accessible and created if it doesn't exist.
        The DatabaseSessionService handles loading persistent history.
        """
        if session_id not in adk_sessions:
            app.logger.info(f"Initializing ADK session check for {USER_ID}/{session_id}")
            
            try:
                session = await session_service.get_session(
                    app_name=APP_NAME, 
                    user_id=USER_ID, 
                    session_id=session_id
                )
                
                if not session:
                    await session_service.create_session(
                        app_name=APP_NAME,
                        user_id=USER_ID,
                        session_id=session_id
                    )

            except Exception as e:
                app.logger.error(f"DatabaseSessionService Initialization Error: {e}")
                raise 
            
            adk_sessions[session_id] = True


# --- Helper to get/create session ID from request ---
def get_or_create_session_id():
    """Gets the session ID from the request or generates a new one."""
    session_id = request.args.get('session_id')
    if not session_id:
        # Generate a new, short, URL-safe session ID (e.g., 'a3b7c4d8')
        session_id = secrets.token_hex(4)
        # Redirect to the new URL with the session_id query parameter
        return redirect(url_for('index', session_id=session_id))
    return session_id

# --- API Endpoints ---

@app.route('/history', methods=['GET'])
def get_history_api():
    """Returns the chat history and all sessions for the current session ID."""
    current_session_id = request.args.get('session_id')
    if not current_session_id:
        # Return an empty set if no session ID is provided, but this shouldn't happen 
        # as the index route should always ensure one is present.
        return jsonify({"history": [], "sessions": []}), 200

    history = load_history(current_session_id)
    sessions = get_all_session_ids()
    
    return jsonify({
        "history": history,
        "current_session_id": current_session_id,
        "sessions": sessions
    })

@app.route('/chat', methods=['POST'])
def chat():
    """Handles incoming user messages, runs the ADK agent, and returns the response."""
    current_session_id = request.args.get('session_id')
    if not current_session_id:
        return jsonify({"response": "Error: Session ID is missing."}), 400

    if not runner:
        return jsonify({"response": "Error: Agent runner is not initialized. Check server logs."}), 500

    # Ensure the ADK session is initialized/loaded from the database
    if root_agent and current_session_id not in adk_sessions:
        try:
            # Synchronously call the async session initializer
            asyncio.run(initialize_adk_session(current_session_id))
        except Exception as e:
            app.logger.error(f"ADK Session Initialization Error: {e}")
            return jsonify({"response": f"ADK Session Init Error: {str(e)}"}), 500

    data = request.get_json()
    user_input = data.get('message', '').strip()

    if not user_input:
        return jsonify({"response": "Please provide a message."}), 400

    # 1. Save user message to UI history DB (history.db)
    save_message(current_session_id, "user", user_input)

    # Prepare the message for the runner
    message = Content(role="user", parts=[Part(text=user_input)])

    response_text = "Sorry, I encountered an internal error."

    async def get_agent_response(msg, session_id):
        """Asynchronously runs the agent and extracts the final text response."""
        response = ""
        try:
            async for event in runner.run_async(
                user_id=USER_ID,
                session_id=session_id,
                new_message=msg
            ):
                if hasattr(event, "is_final_response") and event.is_final_response():
                    if hasattr(event, "content") and event.content.parts:
                        # Extract text from the first part of the content
                        response = event.content.parts[0].text
                        break
        except Exception as e:
            # Handle potential ADK/Runner exceptions
            return f"An agent error occurred: {str(e)}"
        
        return response

    try:
        final_response = asyncio.run(get_agent_response(message, current_session_id))
        
        if final_response.startswith("An agent error occurred"):
            response_text = final_response
            status_code = 500
        else:
            response_text = final_response
            status_code = 200
            
            # 2. Save agent message to UI history DB (history.db) on success
            save_message(current_session_id, "agent", response_text)

    except Exception as e:
        response_text = f"Flask runtime error: {str(e)}"
        status_code = 500
        
    return jsonify({"response": response_text}), status_code


@app.route('/')
def index():
    """Handles dynamic session creation and serves the main chat application page."""
    
    # 1. Check for/create session_id and handle redirect if needed
    session_id_result = get_or_create_session_id()
    
    if isinstance(session_id_result, Response): 
        # This is a redirect object returned by get_or_create_session_id
        return session_id_result
    
    current_session_id = session_id_result

    # 2. Render the index.html template, passing the current session ID
    return render_template('index.html', current_session_id=current_session_id)

# --- Run the Flask App ---
if __name__ == "__main__":
    # Initialize database when the application starts
    init_db()
    # To run this file, you'll need the required dependencies and instance/agent.py
    app.run(debug=True, host='0.0.0.0', port=5000)
