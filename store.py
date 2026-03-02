import json
import os
from datetime import datetime

# All reservations live in one file — one record per booking
RESERVATIONS_FILE = "data/reservations.json"


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_all() -> list:
    """Load the full reservations list from disk. Returns [] if file doesn't exist."""
    if not os.path.exists(RESERVATIONS_FILE):
        return []
    with open(RESERVATIONS_FILE, "r") as f:
        return json.load(f)


def _save_all(records: list):
    """Write the full reservations list back to disk."""
    os.makedirs("data", exist_ok=True)
    with open(RESERVATIONS_FILE, "w") as f:
        json.dump(records, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def save_reservation(record: dict):
    """
    Append a new reservation record to the store.

    Minimum expected fields:
      student_id, exam_id, exam_description, modality, booked_at, status

    For vendor_interface: also include launch_url
    For direct_booking:   also include reservation_no, booked_slot, management_url
    """
    records = _load_all()

    # Always stamp when it was saved
    record["booked_at"] = record.get("booked_at") or datetime.now().isoformat()
    record["status"]    = record.get("status", "booked")

    records.append(record)
    _save_all(records)

    print(f"✓ Reservation saved to store  ({RESERVATIONS_FILE})")


def get_reservations_for_user(student_id: str) -> list:
    """Return all reservations for a given student_id."""
    return [r for r in _load_all() if r.get("student_id") == student_id]


def get_reservation_by_no(reservation_no) -> dict | None:
    """
    Look up a single reservation by ProctorU's reservation_no.
    Used for cancel / reschedule flows later.
    """
    for r in _load_all():
        if str(r.get("reservation_no")) == str(reservation_no):
            return r
    return None


def get_reservation_by_student_exam(student_id: str, exam_id: str) -> dict | None:
    """
    Look up a reservation by student_id + exam_id.
    Used by the webhook handler to match incoming ProctorU events
    to our local records for the vendor_interface flow.
    Returns the most recent matching record.
    """
    matches = [
        r for r in _load_all()
        if r.get("student_id") == student_id and r.get("exam_id") == exam_id
    ]
    return matches[-1] if matches else None


def update_reservation_status(reservation_no, new_status: str):
    """
    Update the status of a reservation by reservation_no.

    Valid statuses:
      'pending_scheduling' → vendor_interface: URL sent, user hasn't scheduled on ProctorU yet
      'booked'             → direct_booking: confirmed immediately via API
      'scheduled'          → vendor_interface: webhook received, user completed scheduling
      'cancelled'          → either flow: cancelled via API (Phase 5)
      'rescheduled'        → either flow: moved to new slot (Phase 5)

    Called by:
      - webhook_handler.py when ProctorU fires a scheduling confirmation (vendor_interface)
      - cancel/reschedule flows (Phase 5)
    """
    records = _load_all()
    updated = False

    for r in records:
        if str(r.get("reservation_no")) == str(reservation_no):
            r["status"] = new_status
            r["updated_at"] = datetime.now().isoformat()
            updated = True
            break

    if updated:
        _save_all(records)
        print(f"✓ Reservation {reservation_no} status updated to '{new_status}'")
    else:
        print(f"⚠ Reservation {reservation_no} not found in store")


def list_upcoming_for_user(student_id: str) -> list:
    """
    Return booked (not cancelled) reservations for a user
    where booked_slot is in the future. Used for dashboard later.
    """
    now = datetime.now().isoformat()
    results = []

    for r in get_reservations_for_user(student_id):
        if r.get("status") == "cancelled":
            continue
        # vendor_interface reservations have no slot time (user picks on ProctorU's site)
        # Include them always; filter direct_booking by slot time
        slot = r.get("booked_slot", "")
        if not slot or slot > now:
            results.append(r)

    return results
