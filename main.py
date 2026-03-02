import json
from api_client import add_bluebird_exam, get_available_slots, book_adhoc_appointment
from utils import (
    to_windows_timezone,
    generate_reservation_id,
    parse_slots,
    find_matching_slot,
    format_slot_time,
    display_slots,
    prompt_slot_selection
)
from store import save_reservation


# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

def load_exam_config(exam_id: str) -> dict:
    """Looks up an exam by exam_id from config/exams.json."""
    with open("config/exams.json") as f:
        all_exams = json.load(f)

    for exam in all_exams["exams"]:
        if exam["exam_id"] == exam_id:
            return exam

    raise ValueError(f"Exam '{exam_id}' not found in config/exams.json")


# ─────────────────────────────────────────────────────────────────────────────
# Flow A: Vendor Interface (BlueBird / ProctorU-hosted scheduling)
# ─────────────────────────────────────────────────────────────────────────────

def vendor_interface_flow(user: dict, exam: dict):
    """
    Calls addBlueBirdExam — registers the exam for the user.
    ProctorU returns a launch URL; user clicks it to schedule on their site.
    """
    result = add_bluebird_exam(user, exam)

    print("\n─── Vendor Interface Flow Result ─────────────────────────")
    print(json.dumps(result, indent=2))

    # Check for API-level errors first (HTTP 200 but response_code != 1 means failure)
    response_code = result.get("response", {}).get("response_code")
    if response_code != 1:
        message = result.get("response", {}).get("message", "Unknown error")
        print(f"\n⚠ API returned response_code={response_code}: {message}")
        print("  Check the saved response file for details.")
        return

    # data can be null/None on failure — use (... or {}) to safely call .get()
    data = result.get("response", {}).get("data") or {}

    launch_url = (
        data.get("launch_url") or
        data.get("url") or
        result.get("response", {}).get("launch_url") or
        result.get("response", {}).get("url")
    )

    if launch_url:
        print(f"\n✓ Launch URL ready: {launch_url}")
        print("  → User clicks this link to schedule on ProctorU's site.")

        # Persist to store — status is "pending_scheduling" because the user still needs
        # to click this URL and complete scheduling on ProctorU's site.
        # Status will be updated to "scheduled" when we receive the ProctorU webhook.
        save_reservation({
            "student_id":       user["student_id"],
            "exam_id":          exam["exam_id"],
            "exam_description": exam["description"],
            "modality":         "vendor_interface",
            "launch_url":       launch_url,
            "status":           "pending_scheduling"
        })
    else:
        print("\n⚠ No launch URL found — check the saved response file for the full payload.")


# ─────────────────────────────────────────────────────────────────────────────
# Flow B: Direct Booking (availability check → slot select → book)
# ─────────────────────────────────────────────────────────────────────────────

def direct_booking_flow(user: dict, exam: dict, preferred_datetime: str):
    """
    Step 1 — Check available slots for the user's preferred date.
    Step 2 — If preferred time is available: confirm and book.
             If not: show all slots that day and let user pick.
    Step 3 — Book the selected slot via addAdHocProcess.

    Args:
        user               : user profile dict
        exam               : exam config dict
        preferred_datetime : user's preferred date/time — format "YYYY-MM-DD HH:MM"
                             e.g. "2026-03-15 09:00"
    """

    print(f"\n─── Direct Booking Flow ──────────────────────────────────")
    print(f"  Exam      : {exam['description']}")
    print(f"  Preferred : {preferred_datetime}")

    # Build the start_date param for the availability API (ISO format with Z)
    date_part = preferred_datetime.split(" ")[0]
    start_date_param = f"{date_part}T00:00:00Z"

    # AdHoc APIs require Windows timezone format
    windows_tz = to_windows_timezone(user["time_zone_id"])

    # ── Step 1: Get available slots ──────────────────────────────────────────
    availability_result = get_available_slots(
        time_zone_id=windows_tz,
        start_date=start_date_param,
        duration=exam["duration"]
    )

    slots = parse_slots(availability_result["response"])

    if not slots:
        print("\n✗ No slots returned by the API.")
        print("  Check the saved response file — the response shape may differ.")
        print(f"  Raw response: {json.dumps(availability_result['response'], indent=2)}")
        return

    print(f"\n  {len(slots)} slot(s) found for {date_part}.")

    # ── Step 2: Check if preferred time is available ──────────────────────────
    matching_slot = find_matching_slot(slots, preferred_datetime)

    if matching_slot:
        print(f"\n✓ Your preferred time is available: {format_slot_time(matching_slot)}")
        confirm = input("  Confirm this slot? (y/n): ").strip().lower()
        if confirm != "y":
            print("\n  OK — showing all available slots so you can pick another:")
            selected_slot = prompt_slot_selection(slots)
        else:
            selected_slot = matching_slot
    else:
        print(f"\n⚠ Your preferred time ({preferred_datetime}) is not available.")
        print("  Here are all available slots for that day:")
        selected_slot = prompt_slot_selection(slots)

    # ── Step 3: Confirm selection and book ────────────────────────────────────
    print(f"\n  You selected: {format_slot_time(selected_slot)}")
    final_confirm = input("  Book this slot? (y/n): ").strip().lower()

    if final_confirm != "y":
        print("\n  Booking cancelled. No reservation was made.")
        return

    # Attach Windows timezone to user dict for the booking call
    user["time_zone_id_windows"] = windows_tz

    reservation_id = generate_reservation_id()
    booking_result = book_adhoc_appointment(user, exam, selected_slot, reservation_id)

    print("\n─── Booking Result ───────────────────────────────────────")
    print(json.dumps(booking_result, indent=2))

    if booking_result["status_code"] == 200:
        # Pull confirmed details from ProctorU's response
        response_data     = booking_result.get("response", {}).get("data", {})
        reservation_no    = response_data.get("reservation_no", "N/A")  # ProctorU's internal ID
        management_url    = response_data.get("url", "")                # URL to manage reservation
        response_code     = booking_result.get("response", {}).get("response_code")

        if response_code != 1:
            # API returned 200 HTTP but flagged an issue at the application level
            message = booking_result.get("response", {}).get("message", "Unknown error")
            print(f"\n⚠ HTTP 200 but API response_code={response_code}: {message}")
            print("  Check the saved response file for details.")
            return

        print(f"\n✓ Appointment booked successfully!")
        print(f"  ─────────────────────────────────────────")
        print(f"  Exam             : {exam['description']}")
        print(f"  Student          : {user['first_name']} {user['last_name']}")
        print(f"  Slot             : {format_slot_time(selected_slot)}")
        print(f"  Reservation No   : {reservation_no}")    # ProctorU's ID — use for cancel/reschedule
        print(f"  Our Res UUID     : {reservation_id}")    # Our internal tracking ID
        if management_url:
            print(f"  Manage Appt URL  : {management_url}")
        print(f"  ─────────────────────────────────────────")
        print(f"\n  → Keep the Reservation No ({reservation_no}) — needed to cancel or reschedule.")

        # Persist to store — full record including ProctorU's reservation_no
        save_reservation({
            "student_id":       user["student_id"],
            "exam_id":          exam["exam_id"],
            "exam_description": exam["description"],
            "modality":         "direct_booking",
            "reservation_id":   reservation_id,     # Our UUID
            "reservation_no":   reservation_no,     # ProctorU's ID (for cancel/reschedule)
            "booked_slot":      booking_result["booked_slot"],
            "management_url":   management_url,
            "status":           "booked"
        })
    else:
        print(f"\n✗ Booking failed (HTTP {booking_result['status_code']}).")
        print("  Check the saved response file for details.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():

    # ── Simulated user profile ────────────────────────────────────────────────
    # In the future this comes from the CMS/LMS session
    user = {
        "student_id":    "84a85485",
        "first_name":    "Jane",
        "last_name":     "Does",
        "email":         "indijones12@yopmail.com",
        "user_password": "9ea342D9e48b",            # Must have uppercase + lowercase + digit
        "time_zone_id":  "America/Chicago",       # IANA format — we convert as needed

        # Address fields — will come from CMS user profile later
        "address1":      "2200 Riverchase Center",
        "city":          "Birmingham",
        "state":         "IL",
        "country":       "US",
        "zipcode":       "60193",
        "phone1":        "8557728678"
    }

    # ── Simulated chatbot inputs ──────────────────────────────────────────────
    # In the future these come from the chatbot conversation
    exam_id            = "AdHocCertID"       # user says: "I want to take AdHoc Demo Exam"
    preferred_datetime = "2026-03-15 09:00"  # user says: "March 15th at 9am"

    # ── Load exam config and route by modality ────────────────────────────────
    exam = load_exam_config(exam_id)
    print(f"\nExam loaded   : {exam['description']}")
    print(f"Modality      : {exam['modality']}")

    if exam["modality"] == "vendor_interface":
        vendor_interface_flow(user, exam)

    elif exam["modality"] == "direct_booking":
        direct_booking_flow(user, exam, preferred_datetime)

    else:
        print(f"\n⚠ Unknown modality: '{exam['modality']}' — nothing to do yet.")


if __name__ == "__main__":
    main()
