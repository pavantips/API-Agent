import os
import uuid
from flask import Flask, render_template, request, jsonify, session
from anthropic import Anthropic
from dotenv import load_dotenv

from agent import dispatch_tool, TOOLS, build_system_prompt, SESSION_USER
from store import get_reservations_for_user

load_dotenv()

app = Flask(__name__)
# Secret key signs the session cookie (only stores session_id — not the conversation)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

# ─────────────────────────────────────────────────────────────────────────────
# In-memory conversation store { session_id: [messages] }
# Fine for a single-user demo. Phase 5 → swap for Redis or DB.
# ─────────────────────────────────────────────────────────────────────────────
conversations: dict = {}


def run_agent_turn(conversation: list, user_message: str) -> tuple:
    """
    Append user_message to conversation, run the Claude agent loop,
    and return (bot_text_response, list_of_tool_names_called).
    """
    client = Anthropic()
    system_prompt = build_system_prompt()
    tool_calls_made = []

    conversation.append({"role": "user", "content": user_message})

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=system_prompt,
            tools=TOOLS,
            messages=conversation,
        )

        # Append full content block list — preserves tool_use blocks in history
        conversation.append({"role": "assistant", "content": response.content})

        # ── Claude finished ───────────────────────────────────────────────────
        if response.stop_reason == "end_turn":
            bot_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    bot_text = block.text
            return bot_text, tool_calls_made

        # ── Claude wants to call tools ────────────────────────────────────────
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_calls_made.append(block.name)
                    result = dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result,
                    })
            conversation.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop_reason — exit to avoid infinite loop
        break

    return "", tool_calls_made


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Main page — renders the chat UI and current bookings dashboard."""
    reservations = get_reservations_for_user(SESSION_USER["student_id"])
    return render_template("index.html", user=SESSION_USER, reservations=reservations)


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    POST { "message": "..." }
    Returns { "response": "...", "tool_calls": [...], "reservations": [...] }
    """
    data = request.get_json() or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Identify this browser session
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    session_id = session["session_id"]

    if session_id not in conversations:
        conversations[session_id] = []

    try:
        bot_text, tool_calls = run_agent_turn(conversations[session_id], user_message)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    reservations = get_reservations_for_user(SESSION_USER["student_id"])

    return jsonify({
        "response":     bot_text,
        "tool_calls":   tool_calls,
        "reservations": reservations,
    })


@app.route("/api/reservations")
def get_reservations():
    """GET — fetch latest reservations for the dashboard."""
    reservations = get_reservations_for_user(SESSION_USER["student_id"])
    return jsonify(reservations)


@app.route("/api/reset", methods=["POST"])
def reset_chat():
    """POST — clear conversation history for this session."""
    session_id = session.get("session_id")
    if session_id and session_id in conversations:
        del conversations[session_id]
    return jsonify({"status": "ok"})


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  Exam Scheduling Agent — Web UI")
    print("  Open: http://localhost:5001\n")
    # Port 5001 avoids conflict with macOS AirPlay (which uses 5000)
    app.run(debug=True, port=5001)
