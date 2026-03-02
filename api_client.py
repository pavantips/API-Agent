import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

PROCTORU_BASE_URL = "https://api.proctoru.com/api"
PROCTORU_AUTH_TOKEN = os.getenv("PROCTORU_AUTH_TOKEN")


def add_bluebird_exam(user: dict, exam: dict) -> dict:
    """
    Calls ProctorU's addBlueBirdExam endpoint.

    This is the vendor_interface flow — ProctorU registers the exam for the user
    and returns a launch URL. The user clicks that URL to go schedule on ProctorU's site.

    Args:
        user: dict with student_id, first_name, last_name, email, user_password, time_zone_id
        exam: dict loaded from config/exams.json for a single exam

    Returns:
        dict with the full API response (and saved to responses/ folder)
    """

    if not PROCTORU_AUTH_TOKEN:
        raise EnvironmentError("PROCTORU_AUTH_TOKEN is not set. Check your .env file.")

    endpoint = f"{PROCTORU_BASE_URL}/addBlueBirdExam"

    headers = {
        "Authorization-Token": PROCTORU_AUTH_TOKEN,
        "Content-Type": "application/json"
    }

    # Build the request body by merging user data and exam config
    body = {
        "student_id":   user["student_id"],
        "first_name":   user["first_name"],
        "last_name":    user["last_name"],
        "user_id":      user["student_id"],
        "user_password": user["user_password"],
        "email":        user["email"],
        "time_zone_id": user["time_zone_id"],

        "exam_id":      exam["exam_id"],
        "description":  exam["description"],
        "duration":     exam["duration"],
        "exam_url":     exam["exam_url"],
        "exam_password": exam["exam_password"],
        "instructor":   exam["instructor"],
        "active_date":  exam["active_date"],
        "end_date":     exam["end_date"]
    }

    print(f"\n→ Calling addBlueBirdExam for exam: {exam['exam_id']}")
    print(f"  Endpoint : {endpoint}")
    print(f"  Student  : {user['first_name']} {user['last_name']} ({user['email']})\n")

    response = requests.post(endpoint, headers=headers, json=body)

    result = {
        "status_code": response.status_code,
        "exam_id": exam["exam_id"],
        "student_id": user["student_id"],
        "called_at": datetime.now().isoformat(),
        "response": {}
    }

    if response.ok:
        result["response"] = response.json()
    else:
        result["response"] = {
            "error": response.text
        }

    save_response(result, label="add_bluebird_exam")
    return result


def get_available_slots(time_zone_id: str, start_date: str, duration: int,
                        isadhoc: str = "Y", takeitnow: str = "N") -> dict:
    """
    Calls ProctorU's getScheduleInfoAvailableTimesList endpoint.

    Returns available time slots for a given date and duration.
    The response is saved to responses/ and returned for slot selection logic.

    Args:
        time_zone_id : Windows timezone string e.g. "Central Standard Time"
        start_date   : ISO datetime string e.g. "2026-03-15T00:00:00Z"
        duration     : Exam duration in minutes e.g. 60
        isadhoc      : "Y" = adhoc/on-demand pool, "N" = standard scheduled
        takeitnow    : "N" = future scheduled slot (set to "Y" later for on-demand)
    """

    if not PROCTORU_AUTH_TOKEN:
        raise EnvironmentError("PROCTORU_AUTH_TOKEN is not set. Check your .env file.")

    endpoint = f"{PROCTORU_BASE_URL}/getScheduleInfoAvailableTimesList/"

    # Note: This API uses query params AND a start_date header (matching Postman exactly)
    params = {
        "time_zone_id": time_zone_id,
        "isadhoc":      isadhoc,
        "start_date":   start_date,
        "takeitnow":    takeitnow,
        "duration":     duration
    }

    headers = {
        "Authorization-Token": PROCTORU_AUTH_TOKEN,
        "start_date":          start_date   # API expects this in header too (per Postman)
    }

    print(f"\n→ Calling getScheduleInfoAvailableTimesList")
    print(f"  Date     : {start_date}")
    print(f"  Timezone : {time_zone_id}")
    print(f"  Duration : {duration} mins\n")

    # POST with empty body — params go in the URL (matching Postman)
    response = requests.post(endpoint, headers=headers, params=params, data="")

    result = {
        "status_code": response.status_code,
        "requested_date": start_date,
        "called_at": datetime.now().isoformat(),
        "response": {}
    }

    if response.ok:
        result["response"] = response.json()
    else:
        result["response"] = {"error": response.text}

    save_response(result, label="get_available_slots")
    return result


def book_adhoc_appointment(user: dict, exam: dict,
                           selected_slot: dict, reservation_id: str) -> dict:
    """
    Calls ProctorU's addAdHocProcess endpoint to book the chosen slot.

    Args:
        user            : dict with student/user profile data
        exam            : dict from config/exams.json
        selected_slot   : the slot dict the user picked from get_available_slots()
        reservation_id  : UUID we generated — store this for cancel/reschedule later

    Returns:
        dict with full API response (saved to responses/)
    """

    if not PROCTORU_AUTH_TOKEN:
        raise EnvironmentError("PROCTORU_AUTH_TOKEN is not set. Check your .env file.")

    endpoint = f"{PROCTORU_BASE_URL}/addAdHocProcess/"

    headers = {
        "Authorization-Token": PROCTORU_AUTH_TOKEN,
        "Content-Type": "application/json"
    }

    # Extract the slot's start time — key name TBD until we see a live response
    # We'll lock this down once we see the actual availability response shape
    slot_start = selected_slot.get("start_date") or \
                 selected_slot.get("start_time") or \
                 selected_slot.get("startDate") or \
                 ""

    body = {
        "student_id":    user["student_id"],
        "user_password": user["user_password"],
        "last_name":     user["last_name"],
        "first_name":    user["first_name"],
        "address1":      user.get("address1", ""),
        "city":          user.get("city", ""),
        "state":         user.get("state", ""),
        "country":       user.get("country", "US"),
        "zipcode":       user.get("zipcode", ""),
        "phone1":        user.get("phone1", ""),
        "email":         user["email"],
        "time_zone_id":  user["time_zone_id_windows"],  # Windows format required here
        "description":   exam["description"],
        "duration":      str(exam["duration"]),
        "notes":         "",
        "start_date":    slot_start,                   # Exact slot time user picked
        "reservation_id": reservation_id,              # UUID we generated
        "takeitnow":     "N",
        "notify":        "Y"
    }

    print(f"\n→ Calling addAdHocProcess (booking appointment)")
    print(f"  Exam     : {exam['description']}")
    print(f"  Student  : {user['first_name']} {user['last_name']}")
    print(f"  Slot     : {slot_start}")
    print(f"  Res ID   : {reservation_id}\n")

    response = requests.post(endpoint, headers=headers, json=body)

    result = {
        "status_code":    response.status_code,
        "reservation_id": reservation_id,    # Our UUID
        "reservation_no": None,              # ProctorU's internal ID — filled in below
        "exam_id":        exam["exam_id"],
        "student_id":     user["student_id"],
        "booked_slot":    slot_start,
        "called_at":      datetime.now().isoformat(),
        "response":       {}
    }

    if response.ok:
        result["response"] = response.json()
        # Extract and surface ProctorU's reservation_no for easy access
        result["reservation_no"] = result["response"].get("data", {}).get("reservation_no")
    else:
        result["response"] = {"error": response.text}

    save_response(result, label="book_adhoc_appointment")
    return result


def save_response(data: dict, label: str):
    """Saves API response to responses/ folder with a timestamp in the filename."""

    os.makedirs("responses", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"responses/{label}_{timestamp}.json"

    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

    print(f"✓ Response saved to: {filename}")
