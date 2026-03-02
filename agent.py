import os
import json
from datetime import datetime
from anthropic import Anthropic
from dotenv import load_dotenv

from api_client import (
    add_bluebird_exam,
    get_available_slots as api_get_slots,
    book_adhoc_appointment,
)
from utils import (
    to_windows_timezone,
    generate_reservation_id,
    parse_slots,
    format_slot_time,
)
from store import save_reservation, get_reservations_for_user
from main import load_exam_config

load_dotenv()


# ─────────────────────────────────────────────────────────────────────────────
# Session user — Phase 5: replace with real CMS/LMS lookup
# ─────────────────────────────────────────────────────────────────────────────

SESSION_USER = {
    "student_id":    "84a85485",
    "first_name":    "Jane",
    "last_name":     "Does",
    "email":         "indijones12@yopmail.com",
    "user_password": "9ea342D9e48b",       # Must have uppercase + lowercase + digit
    "time_zone_id":  "America/Chicago",

    # Address fields — will come from CMS user profile in Phase 5
    "address1":      "2200 Riverchase Center",
    "city":          "Birmingham",
    "state":         "IL",
    "country":       "US",
    "zipcode":       "60193",
    "phone1":        "8557728678",
}


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations — what actually runs when Claude requests a tool call
# ─────────────────────────────────────────────────────────────────────────────

def tool_get_exam_list() -> str:
    """Return all available exams as a JSON string."""
    with open("config/exams.json") as f:
        data = json.load(f)

    simplified = [
        {
            "exam_id":          e["exam_id"],
            "description":      e["description"],
            "duration_minutes": e["duration"],
            "modality":         e["modality"],
        }
        for e in data["exams"]
    ]
    return json.dumps(simplified, indent=2)


def tool_check_availability(exam_id: str, date: str) -> str:
    """
    Check available slots for a given exam on a given date.
    date must be in YYYY-MM-DD format.
    Returns a numbered list of available slots with their exact start_date strings.
    """
    try:
        exam = load_exam_config(exam_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    windows_tz       = to_windows_timezone(SESSION_USER["time_zone_id"])
    start_date_param = f"{date}T00:00:00Z"

    result = api_get_slots(
        time_zone_id=windows_tz,
        start_date=start_date_param,
        duration=exam["duration"],
    )

    slots = parse_slots(result["response"])

    if not slots:
        return json.dumps({
            "available_slots": [],
            "message": f"No slots available on {date}. Try a different date.",
        })

    slot_list = [
        {
            "slot_number": i,
            "start_date":  s.get("start_date", ""),
            "formatted":   format_slot_time(s),
        }
        for i, s in enumerate(slots, start=1)
    ]

    return json.dumps({
        "date":            date,
        "exam_id":         exam_id,
        "total_slots":     len(slot_list),
        "available_slots": slot_list,
    }, indent=2)


def tool_book_slot(exam_id: str, slot_start_date: str) -> str:
    """
    Book a specific time slot for a direct_booking exam.
    slot_start_date must be the exact start_date string from check_availability.
    """
    try:
        exam = load_exam_config(exam_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    user = SESSION_USER.copy()
    user["time_zone_id_windows"] = to_windows_timezone(user["time_zone_id"])

    selected_slot  = {"start_date": slot_start_date}
    reservation_id = generate_reservation_id()

    booking_result = book_adhoc_appointment(user, exam, selected_slot, reservation_id)

    # ── HTTP-level failure ────────────────────────────────────────────────────
    if booking_result["status_code"] != 200:
        return json.dumps({
            "success": False,
            "error":   f"HTTP {booking_result['status_code']}",
            "details": booking_result.get("response", {}),
        })

    # ── Application-level failure (HTTP 200 but response_code != 1) ───────────
    response_code = booking_result.get("response", {}).get("response_code")
    if response_code != 1:
        message = booking_result.get("response", {}).get("message", "Unknown error")
        return json.dumps({
            "success": False,
            "error":   f"API error (response_code={response_code}): {message}",
        })

    response_data  = booking_result.get("response", {}).get("data", {})
    reservation_no = response_data.get("reservation_no", "N/A")
    management_url = response_data.get("url", "")

    # Persist the booking
    save_reservation({
        "student_id":       user["student_id"],
        "exam_id":          exam["exam_id"],
        "exam_description": exam["description"],
        "modality":         "direct_booking",
        "reservation_id":   reservation_id,   # Our UUID
        "reservation_no":   reservation_no,   # ProctorU's internal ID (for cancel/reschedule)
        "booked_slot":      slot_start_date,
        "management_url":   management_url,
        "status":           "booked",
    })

    return json.dumps({
        "success":        True,
        "exam":           exam["description"],
        "slot":           format_slot_time(selected_slot),
        "reservation_no": reservation_no,
        "reservation_id": reservation_id,
        "management_url": management_url,
        "message": (
            f"Appointment booked! Reservation No: {reservation_no}. "
            "Save this number — you'll need it to cancel or reschedule."
        ),
    }, indent=2)


def tool_register_vendor_exam(exam_id: str) -> str:
    """
    Register a vendor_interface exam via the BlueBird API.
    Returns a launch URL the student clicks to schedule on ProctorU's site.
    """
    try:
        exam = load_exam_config(exam_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    result = add_bluebird_exam(SESSION_USER, exam)

    response_code = result.get("response", {}).get("response_code")
    if response_code != 1:
        message = result.get("response", {}).get("message", "Unknown error")
        return json.dumps({
            "success": False,
            "error":   f"API error (response_code={response_code}): {message}",
        })

    data       = result.get("response", {}).get("data") or {}
    launch_url = (
        data.get("launch_url") or
        data.get("url") or
        result.get("response", {}).get("launch_url") or
        result.get("response", {}).get("url")
    )

    if not launch_url:
        return json.dumps({
            "success": False,
            "error":   "No launch URL returned. Check responses/ folder for details.",
        })

    # Persist as pending_scheduling — status becomes 'scheduled' when webhook fires
    save_reservation({
        "student_id":       SESSION_USER["student_id"],
        "exam_id":          exam["exam_id"],
        "exam_description": exam["description"],
        "modality":         "vendor_interface",
        "launch_url":       launch_url,
        "status":           "pending_scheduling",
    })

    return json.dumps({
        "success":    True,
        "exam":       exam["description"],
        "launch_url": launch_url,
        "status":     "pending_scheduling",
        "message":    (
            "Exam registered! Share this URL with the student — "
            "they click it to pick their time slot on ProctorU's site."
        ),
    }, indent=2)


def tool_get_my_reservations() -> str:
    """Return all reservations for the current student."""
    reservations = get_reservations_for_user(SESSION_USER["student_id"])
    if not reservations:
        return json.dumps({"reservations": [], "message": "No reservations found."})
    return json.dumps({"reservations": reservations}, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions — Claude sees these to decide when and how to call each tool
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_exam_list",
        "description": (
            "Return the list of all available exams the student can schedule. "
            "Always call this first to see which exams exist and their modalities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Check available appointment slots for a given exam on a specific date. "
            "Only use this for exams with modality='direct_booking'. "
            "Returns a numbered list of available time slots with their exact start_date strings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "exam_id": {
                    "type":        "string",
                    "description": "The exam_id from get_exam_list",
                },
                "date": {
                    "type":        "string",
                    "description": "Date to check in YYYY-MM-DD format, e.g. '2026-03-20'",
                },
            },
            "required": ["exam_id", "date"],
        },
    },
    {
        "name": "book_slot",
        "description": (
            "Book a specific time slot for a direct_booking exam. "
            "IMPORTANT: Always show the student the available slots and get their "
            "explicit verbal confirmation BEFORE calling this tool. "
            "Use the exact start_date string from check_availability output."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "exam_id": {
                    "type":        "string",
                    "description": "The exam_id from get_exam_list",
                },
                "slot_start_date": {
                    "type":        "string",
                    "description": (
                        "The exact start_date string from check_availability, "
                        "e.g. '2026-03-20T09:00:00Z'"
                    ),
                },
            },
            "required": ["exam_id", "slot_start_date"],
        },
    },
    {
        "name": "register_vendor_exam",
        "description": (
            "Register a vendor_interface exam via the BlueBird API. "
            "Only use this for exams with modality='vendor_interface'. "
            "Returns a launch URL the student clicks to schedule on ProctorU's site. "
            "No date/time is needed from the student for this flow."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "exam_id": {
                    "type":        "string",
                    "description": "The exam_id from get_exam_list",
                },
            },
            "required": ["exam_id"],
        },
    },
    {
        "name": "get_my_reservations",
        "description": "Return all existing reservations for the current student.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatcher — maps tool name → Python function call
# ─────────────────────────────────────────────────────────────────────────────

def dispatch_tool(tool_name: str, tool_input: dict) -> str:
    """Execute the requested tool and return its result as a JSON string."""
    if tool_name == "get_exam_list":
        return tool_get_exam_list()

    elif tool_name == "check_availability":
        return tool_check_availability(
            exam_id=tool_input["exam_id"],
            date=tool_input["date"],
        )

    elif tool_name == "book_slot":
        return tool_book_slot(
            exam_id=tool_input["exam_id"],
            slot_start_date=tool_input["slot_start_date"],
        )

    elif tool_name == "register_vendor_exam":
        return tool_register_vendor_exam(exam_id=tool_input["exam_id"])

    elif tool_name == "get_my_reservations":
        return tool_get_my_reservations()

    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})


# ─────────────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    user  = SESSION_USER
    today = datetime.now().strftime("%A, %B %d, %Y")

    return f"""You are an exam scheduling assistant for {user['first_name']} {user['last_name']}.

Today's date: {today}
Student timezone: {user['time_zone_id']}

Your job:
1. Help the student find and schedule an exam.
2. Start every conversation by calling get_exam_list to see what's available.
3. Route by modality:
   - vendor_interface exams → call register_vendor_exam, then give the student the launch URL
   - direct_booking exams  → ask for a preferred date, call check_availability, present
                             the available slots, get the student's explicit confirmation,
                             THEN call book_slot
4. For direct_booking: always show the available slots and confirm the student's choice
   BEFORE calling book_slot. Never book without a clear "yes" from the student.
5. For vendor_interface: no date/time collection is needed — the student picks on ProctorU's site.
6. Be conversational and friendly. Ask one question at a time.
7. If the student asks to see their existing bookings, call get_my_reservations.

Rules:
- Never call book_slot without the student explicitly confirming the slot.
- Dates for check_availability must be in YYYY-MM-DD format.
- slot_start_date for book_slot must be the exact string from check_availability output.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Agent loop
# ─────────────────────────────────────────────────────────────────────────────

def run_agent():
    client       = Anthropic()
    conversation = []

    print("\n" + "═" * 52)
    print("       Exam Scheduling Agent  (Phase 4)")
    print("═" * 52)
    print(f"  Hi {SESSION_USER['first_name']}! I'm your AI scheduling assistant.")
    print("  Type 'quit' to exit.\n")

    system_prompt = build_system_prompt()

    while True:
        # ── Get user input ────────────────────────────────────────────────────
        try:
            user_input = input("  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  Session ended. Goodbye!\n")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print("\n  Goodbye!\n")
            break

        if not user_input:
            continue

        conversation.append({"role": "user", "content": user_input})

        # ── Inner loop: keep going until Claude stops requesting tools ─────────
        while True:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=4096,
                thinking={"type": "adaptive"},   # Opus 4.6 adaptive thinking
                system=system_prompt,
                tools=TOOLS,
                messages=conversation,
            )

            # Append full content block list — preserves tool_use blocks in history
            conversation.append({"role": "assistant", "content": response.content})

            # ── Claude is done — print its final text response ────────────────
            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        print(f"\n  Bot: {block.text}\n")
                break

            # ── Claude wants to call tools ────────────────────────────────────
            if response.stop_reason == "tool_use":
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        print(f"\n  [→ Agent calling: {block.name}]")
                        result = dispatch_tool(block.name, block.input)
                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": block.id,
                            "content":     result,
                        })

                # Feed all tool results back to Claude in one user message
                conversation.append({"role": "user", "content": tool_results})
                continue   # Loop again — Claude processes the results

            # Unexpected stop_reason — exit inner loop to avoid infinite spin
            break


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        run_agent()
    except KeyboardInterrupt:
        print("\n\n  Session ended. Goodbye!\n")
