import json
from datetime import datetime
from dateutil import parser as date_parser

# Import the flow functions and user profile from main.py
# main.py's entry point is protected by if __name__ == "__main__"
# so importing it here won't trigger anything
from main import vendor_interface_flow, direct_booking_flow


# ─────────────────────────────────────────────────────────────────────────────
# Chat helpers — keeps print formatting consistent throughout
# ─────────────────────────────────────────────────────────────────────────────

def bot_say(message: str):
    """Print a message from the bot."""
    print(f"\n  Bot: {message}")


def divider():
    print("\n" + "─" * 52)


# ─────────────────────────────────────────────────────────────────────────────
# Exam loader
# ─────────────────────────────────────────────────────────────────────────────

def load_all_exams() -> list:
    """Load all exams from config/exams.json."""
    with open("config/exams.json") as f:
        return json.load(f)["exams"]


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Exam selection
# ─────────────────────────────────────────────────────────────────────────────

def ask_exam_selection(exams: list) -> dict:
    """Show numbered exam list and return the exam the user picks."""
    bot_say("Which exam would you like to schedule?")
    print()
    for i, exam in enumerate(exams, start=1):
        print(f"       {i}.  {exam['description']}")

    while True:
        try:
            choice = input("\n  You: ").strip()

            if choice.lower() in ("quit", "exit", "q"):
                print("\n  Goodbye!\n")
                exit(0)

            idx = int(choice) - 1
            if 0 <= idx < len(exams):
                return exams[idx]

            bot_say(f"Please enter a number between 1 and {len(exams)}.")

        except ValueError:
            bot_say("Just type the number next to the exam you want.")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Preferred date (only for direct_booking)
# ─────────────────────────────────────────────────────────────────────────────

def ask_preferred_date() -> datetime:
    """Ask for a preferred date and parse it. Rejects past dates."""
    bot_say("What date would you prefer? (e.g.  March 20,  3/20,  2026-03-20)")

    while True:
        raw = input("\n  You: ").strip()

        if raw.lower() in ("quit", "exit", "q"):
            print("\n  Goodbye!\n")
            exit(0)

        try:
            # Default year/month/day = current year, Jan 1 — so "March 20" fills in correctly
            default = datetime(datetime.now().year, 1, 1)
            dt = date_parser.parse(raw, default=default)

            if dt.date() < datetime.now().date():
                bot_say("That date is in the past. Please choose a future date.")
                continue

            return dt

        except Exception:
            bot_say("I didn't get that date. Try something like 'March 20' or '3/20/2026'.")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Preferred time (only for direct_booking)
# ─────────────────────────────────────────────────────────────────────────────

def ask_preferred_time(date: datetime) -> datetime:
    """Ask for a preferred time and combine it with the already-confirmed date."""
    bot_say(f"What time on {date.strftime('%B %d')}? (e.g.  9am,  2:30pm,  14:00)")

    while True:
        raw = input("\n  You: ").strip()

        if raw.lower() in ("quit", "exit", "q"):
            print("\n  Goodbye!\n")
            exit(0)

        try:
            time_dt = date_parser.parse(raw)

            # Graft the parsed hour/minute onto the confirmed date
            combined = date.replace(
                hour=time_dt.hour,
                minute=time_dt.minute,
                second=0,
                microsecond=0
            )
            return combined

        except Exception:
            bot_say("I didn't get that time. Try something like '9am', '2:30pm', or '14:00'.")


# ─────────────────────────────────────────────────────────────────────────────
# Main chatbot loop
# ─────────────────────────────────────────────────────────────────────────────

def run_chatbot():

    # ── Simulated user profile ────────────────────────────────────────────────
    # Phase 5: replace this with a real CMS/LMS session lookup
    user = {
        "student_id":    "84a85485",
        "first_name":    "Jane",
        "last_name":     "Does",
        "email":         "indijones12@yopmail.com",
        "user_password": "9ea342D9e48b",            # Must have uppercase + lowercase + digit
        "time_zone_id":  "America/Chicago",

        # Address fields — will come from CMS user profile in Phase 5
        "address1":      "2200 Riverchase Center",
        "city":          "Birmingham",
        "state":         "IL",
        "country":       "US",
        "zipcode":       "60193",
        "phone1":        "8557728678"
    }

    # ── Welcome ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 52)
    print("       Exam Scheduling Assistant")
    print("═" * 52)
    print("       (type 'quit' at any time to exit)")

    bot_say(f"Hi {user['first_name']}! I'll help you schedule your exam.")

    # ── Step 1: Exam selection ─────────────────────────────────────────────────
    exams = load_all_exams()
    selected_exam = ask_exam_selection(exams)

    bot_say(f"Got it — \"{selected_exam['description']}\" it is.")

    # ── Step 2: Route by modality ──────────────────────────────────────────────
    modality = selected_exam["modality"]

    if modality == "vendor_interface":
        # No date/time needed from the user —
        # ProctorU handles scheduling on their own platform after we register the exam
        divider()
        bot_say("For this exam, you'll pick your date and time directly on ProctorU's site.")
        bot_say("Let me register the exam and get your scheduling link...")
        divider()

        vendor_interface_flow(user, selected_exam)

        divider()
        bot_say("Done! Click the link above to visit ProctorU and choose your slot.")

    elif modality == "direct_booking":
        # Ask for preferred date + time, then check availability
        preferred_date = ask_preferred_date()
        preferred_time = ask_preferred_time(preferred_date)

        # Format for the flow function: "YYYY-MM-DD HH:MM"
        preferred_datetime_str = preferred_time.strftime("%Y-%m-%d %H:%M")

        divider()
        bot_say(
            f"Checking availability for {preferred_time.strftime('%B %d, %Y at %I:%M %p')}..."
        )
        divider()

        direct_booking_flow(user, selected_exam, preferred_datetime_str)

    else:
        bot_say(
            f"Sorry — I don't know how to handle the '{modality}' modality yet. "
            f"Please contact support."
        )

    divider()
    bot_say("All done! Is there anything else? (Press Enter or type 'quit' to exit)")
    input("\n  You: ")
    print("\n  Goodbye!\n")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        run_chatbot()
    except KeyboardInterrupt:
        print("\n\n  Session ended. Goodbye!\n")
