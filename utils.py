import uuid
from datetime import datetime


# ─────────────────────────────────────────────
# Timezone mapping: IANA → Windows format
# BlueBird exam API uses IANA ("America/Chicago")
# AdHoc availability + booking APIs use Windows ("Central Standard Time")
# ─────────────────────────────────────────────
TIMEZONE_MAP = {
    "America/New_York":    "Eastern Standard Time",
    "America/Chicago":     "Central Standard Time",
    "America/Denver":      "Mountain Standard Time",
    "America/Los_Angeles": "Pacific Standard Time",
    "America/Phoenix":     "US Mountain Standard Time",
    "America/Anchorage":   "Alaskan Standard Time",
    "Pacific/Honolulu":    "Hawaiian Standard Time",
    "UTC":                 "UTC",
}


def to_windows_timezone(iana_tz: str) -> str:
    """Convert IANA timezone string to Windows timezone string.
    Falls back to the original value if not found in the map.
    """
    return TIMEZONE_MAP.get(iana_tz, iana_tz)


def generate_reservation_id() -> str:
    """Generate a unique reservation ID (UUID4).
    This is stored by us and used later for cancel/reschedule.
    """
    return str(uuid.uuid4())


def parse_slots(api_response: dict) -> list:
    """Extract the list of available time slots from the API response.

    ProctorU's response shape is not fully confirmed yet.
    Once we see a live response, update the key path here.

    Expected shape (best guess):
        { "data": [ { "start_date": "...", ... }, ... ] }

    Returns a list of slot dicts. Empty list if nothing found.
    """
    if not api_response:
        return []

    # Try common response shapes — update once we see a live response
    data = api_response.get("data") or \
           api_response.get("slots") or \
           api_response.get("response") or \
           []

    if isinstance(data, list):
        return data

    return []


def format_slot_time(slot: dict) -> str:
    """Return a human-readable time string from a slot dict.
    Tries common field names for the start time.
    """
    raw = slot.get("start_date") or \
          slot.get("start_time") or \
          slot.get("startDate") or \
          slot.get("time") or \
          "Unknown time"

    # Try to parse and reformat for readability
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%B %d, %Y — %I:%M %p")
        except (ValueError, TypeError):
            continue

    return raw  # Return raw string if parsing fails


def find_matching_slot(slots: list, preferred_datetime_str: str) -> dict | None:
    """Check if the user's preferred date/time matches any available slot.

    preferred_datetime_str should be in format: "2026-03-15 09:00"
    Returns the matching slot dict, or None if not found.
    """
    if not preferred_datetime_str:
        return None

    # Normalize preferred time to compare
    try:
        preferred_dt = datetime.strptime(preferred_datetime_str, "%Y-%m-%d %H:%M")
    except ValueError:
        return None

    for slot in slots:
        slot_raw = slot.get("start_date") or slot.get("start_time") or ""
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                slot_dt = datetime.strptime(slot_raw, fmt)
                # Match on date + hour + minute
                if slot_dt.replace(second=0, microsecond=0) == preferred_dt:
                    return slot
            except (ValueError, TypeError):
                continue

    return None


def display_slots(slots: list) -> None:
    """Print the available slots as a numbered list for the user to choose from."""
    print("\nAvailable slots:")
    print("─" * 40)
    for i, slot in enumerate(slots, start=1):
        print(f"  {i}. {format_slot_time(slot)}")
    print("─" * 40)


def prompt_slot_selection(slots: list) -> dict:
    """Show slots and prompt the user to pick one. Returns the chosen slot dict."""
    display_slots(slots)
    while True:
        try:
            choice = int(input(f"\nEnter slot number (1–{len(slots)}): "))
            if 1 <= choice <= len(slots):
                return slots[choice - 1]
            print(f"  Please enter a number between 1 and {len(slots)}.")
        except ValueError:
            print("  Invalid input. Please enter a number.")
