#!/usr/bin/env python3
"""
ProctorU MCP Server
===================
A Model Context Protocol (MCP) server that exposes ProctorU exam scheduling
APIs as tools for any MCP-compatible AI client (Claude Desktop, etc.).

Customer setup — three steps:
  1. pip install mcp requests python-dotenv
  2. Set PROCTORU_AUTH_TOKEN in your environment or a .env file
  3. Add this server to your MCP client config (see claude_desktop_config_example.json)

That's it. The AI handles the conversation; this server handles ProctorU.

Tools exposed:
  check_availability    — Find open time slots for a date & exam duration
  book_exam_slot        — Book a direct-schedule appointment (addAdHocProcess)
  register_vendor_exam  — Register a BlueBird exam and get a ProctorU launch URL
"""

import os
import json
import uuid
import requests
from datetime import datetime
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — customer sets this in their environment
# ─────────────────────────────────────────────────────────────────────────────

PROCTORU_AUTH_TOKEN = os.getenv("PROCTORU_AUTH_TOKEN", "")
PROCTORU_BASE_URL   = "https://api.proctoru.com/api"

if not PROCTORU_AUTH_TOKEN:
    import sys
    print(
        "ERROR: PROCTORU_AUTH_TOKEN is not set.\n"
        "Add it to your .env file or export it in your shell before starting the server.",
        file=sys.stderr,
    )
    # Don't exit — let the server start so the error surfaces in the MCP client UI


# ─────────────────────────────────────────────────────────────────────────────
# Timezone mapping (inlined — no external imports needed)
# ProctorU's BlueBird API uses IANA timezone IDs ("America/Chicago")
# ProctorU's AdHoc APIs use Windows timezone IDs ("Central Standard Time")
# ─────────────────────────────────────────────────────────────────────────────

TIMEZONE_MAP: dict[str, str] = {
    "America/New_York":    "Eastern Standard Time",
    "America/Chicago":     "Central Standard Time",
    "America/Denver":      "Mountain Standard Time",
    "America/Los_Angeles": "Pacific Standard Time",
    "America/Phoenix":     "US Mountain Standard Time",
    "America/Anchorage":   "Alaskan Standard Time",
    "Pacific/Honolulu":    "Hawaiian Standard Time",
    "UTC":                 "UTC",
}


def _to_windows_tz(iana_tz: str) -> str:
    """Convert IANA timezone string → Windows timezone string used by AdHoc APIs."""
    return TIMEZONE_MAP.get(iana_tz, iana_tz)


def _parse_slots(api_response: dict) -> list:
    """Extract the list of slot dicts from a getScheduleInfoAvailableTimesList response."""
    data = (
        api_response.get("data")
        or api_response.get("slots")
        or api_response.get("response")
        or []
    )
    return data if isinstance(data, list) else []


def _format_slot(slot: dict) -> str:
    """Return a human-readable time string from a slot dict."""
    raw = (
        slot.get("start_date")
        or slot.get("start_time")
        or slot.get("startDate")
        or slot.get("time")
        or "Unknown time"
    )
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%B %d, %Y — %I:%M %p")
        except (ValueError, TypeError):
            continue
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# ProctorU API helpers (inlined — no external modules needed)
# ─────────────────────────────────────────────────────────────────────────────

def _call_get_available_slots(
    time_zone_id_windows: str,
    start_date: str,
    duration: int,
) -> dict:
    """
    POST getScheduleInfoAvailableTimesList
    Returns the raw API response dict.
    """
    endpoint = f"{PROCTORU_BASE_URL}/getScheduleInfoAvailableTimesList/"
    params = {
        "time_zone_id": time_zone_id_windows,
        "isadhoc":      "Y",
        "start_date":   start_date,
        "takeitnow":    "N",
        "duration":     duration,
    }
    headers = {
        "Authorization-Token": PROCTORU_AUTH_TOKEN,
        "start_date":          start_date,   # API expects it in the header too
    }
    resp = requests.post(endpoint, headers=headers, params=params, data="", timeout=30)
    if resp.ok:
        return resp.json()
    return {"error": resp.text, "status_code": resp.status_code}


def _call_add_adhoc_process(body: dict) -> dict:
    """POST addAdHocProcess — book a direct-schedule appointment."""
    endpoint = f"{PROCTORU_BASE_URL}/addAdHocProcess/"
    headers = {
        "Authorization-Token": PROCTORU_AUTH_TOKEN,
        "Content-Type":        "application/json",
    }
    resp = requests.post(endpoint, headers=headers, json=body, timeout=30)
    if resp.ok:
        return {"status_code": resp.status_code, "body": resp.json()}
    return {"status_code": resp.status_code, "error": resp.text}


def _call_add_bluebird_exam(body: dict) -> dict:
    """POST addBlueBirdExam — register a vendor_interface exam."""
    endpoint = f"{PROCTORU_BASE_URL}/addBlueBirdExam"
    headers = {
        "Authorization-Token": PROCTORU_AUTH_TOKEN,
        "Content-Type":        "application/json",
    }
    resp = requests.post(endpoint, headers=headers, json=body, timeout=30)
    if resp.ok:
        return {"status_code": resp.status_code, "body": resp.json()}
    return {"status_code": resp.status_code, "error": resp.text}


# ─────────────────────────────────────────────────────────────────────────────
# MCP Server
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP("ProctorU Exam Scheduler")


@mcp.tool()
def check_availability(
    time_zone_id: str,
    date: str,
    duration_minutes: int,
) -> str:
    """
    Check available ProctorU appointment slots for a given date and exam duration.

    Use this before booking to show the student what times are open.

    Args:
        time_zone_id:      Student's IANA timezone, e.g. "America/Chicago".
                           Supported: America/New_York, America/Chicago,
                           America/Denver, America/Los_Angeles, America/Phoenix,
                           America/Anchorage, Pacific/Honolulu, UTC
        date:              Date to check in YYYY-MM-DD format, e.g. "2026-03-20"
        duration_minutes:  Exam duration in minutes, e.g. 120

    Returns:
        JSON with a numbered list of available slots and their exact start_date
        strings. Pass one of those start_date strings to book_exam_slot.
    """
    if not PROCTORU_AUTH_TOKEN:
        return json.dumps({"error": "PROCTORU_AUTH_TOKEN is not configured."})

    windows_tz  = _to_windows_tz(time_zone_id)
    start_param = f"{date}T00:00:00Z"

    raw_response = _call_get_available_slots(
        time_zone_id_windows=windows_tz,
        start_date=start_param,
        duration=duration_minutes,
    )

    if "error" in raw_response:
        return json.dumps({
            "success": False,
            "error":   raw_response["error"],
            "status_code": raw_response.get("status_code"),
        })

    slots = _parse_slots(raw_response)

    if not slots:
        return json.dumps({
            "success":         True,
            "available_slots": [],
            "message": (
                f"No slots available on {date} for {duration_minutes}-minute exam. "
                "Try a different date."
            ),
        })

    slot_list = [
        {
            "slot_number": i,
            "start_date":  s.get("start_date", ""),
            "display":     _format_slot(s),
        }
        for i, s in enumerate(slots, start=1)
    ]

    return json.dumps({
        "success":         True,
        "date":            date,
        "duration_minutes": duration_minutes,
        "timezone":        time_zone_id,
        "total_slots":     len(slot_list),
        "available_slots": slot_list,
        "next_step": (
            "Show these slots to the student. "
            "After they confirm a choice, call book_exam_slot with the exact start_date."
        ),
    }, indent=2)


@mcp.tool()
def book_exam_slot(
    # ── Student profile ──────────────────────────────────────────────────────
    student_id:    str,
    first_name:    str,
    last_name:     str,
    email:         str,
    user_password: str,
    time_zone_id:  str,
    address1:      str,
    city:          str,
    state:         str,
    country:       str,
    zipcode:       str,
    phone1:        str,
    # ── Exam details ─────────────────────────────────────────────────────────
    exam_description:  str,
    duration_minutes:  int,
    slot_start_date:   str,
) -> str:
    """
    Book a ProctorU direct-scheduling appointment via the addAdHocProcess API.

    IMPORTANT: Always call check_availability first, show the student the available
    slots, and get their explicit confirmation BEFORE calling this tool.

    Args:
        student_id:       Unique student identifier from your LMS/CMS
        first_name:       Student first name
        last_name:        Student last name
        email:            Student email address
        user_password:    ProctorU account password — must contain uppercase,
                          lowercase, and a digit (e.g. "MyPass9word")
        time_zone_id:     Student's IANA timezone, e.g. "America/Chicago"
        address1:         Street address
        city:             City
        state:            State code (e.g. "IL")
        country:          Country code (default "US")
        zipcode:          ZIP/postal code
        phone1:           Phone number (digits only, e.g. "8005551234")
        exam_description: Human-readable exam name shown in ProctorU
        duration_minutes: Exam duration in minutes
        slot_start_date:  Exact start_date string from check_availability,
                          e.g. "2026-03-20T09:00:00Z"

    Returns:
        JSON with booking confirmation, ProctorU reservation_no, and a
        management URL for cancel/reschedule.
    """
    if not PROCTORU_AUTH_TOKEN:
        return json.dumps({"error": "PROCTORU_AUTH_TOKEN is not configured."})

    reservation_id = str(uuid.uuid4())

    body = {
        "student_id":    student_id,
        "user_password": user_password,
        "last_name":     last_name,
        "first_name":    first_name,
        "address1":      address1,
        "city":          city,
        "state":         state,
        "country":       country,
        "zipcode":       zipcode,
        "phone1":        phone1,
        "email":         email,
        "time_zone_id":  _to_windows_tz(time_zone_id),   # AdHoc API requires Windows format
        "description":   exam_description,
        "duration":      str(duration_minutes),
        "notes":         "",
        "start_date":    slot_start_date,
        "reservation_id": reservation_id,
        "takeitnow":     "N",
        "notify":        "Y",
    }

    result = _call_add_adhoc_process(body)

    if result.get("status_code") != 200:
        return json.dumps({
            "success": False,
            "error":   f"HTTP {result.get('status_code')}: {result.get('error', 'Unknown error')}",
        })

    api_body      = result.get("body", {})
    response_code = api_body.get("response_code")

    if response_code != 1:
        message = api_body.get("message", "Unknown error")
        return json.dumps({
            "success": False,
            "error":   f"ProctorU error (code={response_code}): {message}",
        })

    data           = api_body.get("data", {})
    reservation_no = data.get("reservation_no", "N/A")
    management_url = data.get("url", "")

    formatted_slot = _format_slot({"start_date": slot_start_date})

    return json.dumps({
        "success":        True,
        "exam":           exam_description,
        "slot":           formatted_slot,
        "slot_start_date": slot_start_date,
        "reservation_id": reservation_id,       # Your internal UUID
        "reservation_no": reservation_no,       # ProctorU's ID (for cancel/reschedule)
        "management_url": management_url,
        "message": (
            f"Appointment booked! ProctorU Reservation #: {reservation_no}. "
            "Store this number — the student will need it to cancel or reschedule."
        ),
    }, indent=2)


@mcp.tool()
def register_vendor_exam(
    # ── Student profile ──────────────────────────────────────────────────────
    student_id:    str,
    first_name:    str,
    last_name:     str,
    email:         str,
    user_password: str,
    time_zone_id:  str,
    # ── Exam configuration ────────────────────────────────────────────────────
    exam_id:       str,
    description:   str,
    duration:      int,
    exam_url:      str,
    exam_password: str,
    instructor:    str,
    active_date:   str,
    end_date:      str,
) -> str:
    """
    Register a ProctorU vendor-interface exam via the addBlueBirdExam API.

    Use this for exams where ProctorU handles scheduling (the student picks
    their own time slot on ProctorU's website after clicking a launch URL).

    No date or time is collected from the student for this flow — ProctorU's
    site handles that. Just call this tool and give the student the launch URL.

    Args:
        student_id:    Unique student identifier from your LMS/CMS
        first_name:    Student first name
        last_name:     Student last name
        email:         Student email address
        user_password: ProctorU account password — must contain uppercase,
                       lowercase, and a digit (e.g. "MyPass9word")
        time_zone_id:  Student's IANA timezone, e.g. "America/Chicago"
        exam_id:       Your exam ID registered with ProctorU
        description:   Human-readable exam name
        duration:      Exam duration in minutes
        exam_url:      URL of the exam content/system
        exam_password: Password for the exam (if applicable)
        instructor:    Instructor name for the exam record
        active_date:   When the exam becomes available, ISO format
                       e.g. "2025-10-06T01:01:00"
        end_date:      When the exam expires, e.g. "9999-12-31T23:59:00"

    Returns:
        JSON with a launch_url the student clicks to schedule on ProctorU's site.
    """
    if not PROCTORU_AUTH_TOKEN:
        return json.dumps({"error": "PROCTORU_AUTH_TOKEN is not configured."})

    body = {
        "student_id":    student_id,
        "first_name":    first_name,
        "last_name":     last_name,
        "user_id":       student_id,
        "user_password": user_password,
        "email":         email,
        "time_zone_id":  time_zone_id,   # BlueBird API uses IANA format directly

        "exam_id":       exam_id,
        "description":   description,
        "duration":      duration,
        "exam_url":      exam_url,
        "exam_password": exam_password,
        "instructor":    instructor,
        "active_date":   active_date,
        "end_date":      end_date,
    }

    result = _call_add_bluebird_exam(body)

    if result.get("status_code") != 200:
        return json.dumps({
            "success": False,
            "error":   f"HTTP {result.get('status_code')}: {result.get('error', 'Unknown error')}",
        })

    api_body      = result.get("body", {})
    response_code = api_body.get("response_code")

    if response_code != 1:
        message = api_body.get("message", "Unknown error")
        return json.dumps({
            "success": False,
            "error":   f"ProctorU error (code={response_code}): {message}",
        })

    data = api_body.get("data") or {}
    launch_url = (
        data.get("launch_url")
        or data.get("url")
        or api_body.get("launch_url")
        or api_body.get("url")
    )

    if not launch_url:
        return json.dumps({
            "success": False,
            "error":   "No launch URL returned. Exam may already be registered.",
            "raw_response": api_body,
        })

    return json.dumps({
        "success":    True,
        "exam":       description,
        "exam_id":    exam_id,
        "student":    f"{first_name} {last_name}",
        "launch_url": launch_url,
        "status":     "pending_scheduling",
        "message": (
            "Exam registered with ProctorU! Share this launch URL with the student. "
            "They click it to choose their own date and time on ProctorU's website."
        ),
    }, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point — run as MCP server over stdio
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
