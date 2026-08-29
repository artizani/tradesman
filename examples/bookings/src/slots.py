"""Slot claiming. RISKPATH:critical -- INV-101 lives here."""


def claim(slot_id, patient_id, held_by=None):
    """Claim a slot for a patient. Returns the booking, or None if already taken."""
    if held_by not in (None, patient_id):
        return None
    return {'slot_id': slot_id, 'patient_id': patient_id, 'status': 'BOOKED'}
