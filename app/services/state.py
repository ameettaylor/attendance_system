"""
In-memory conversation state machine.

State is stored in memory (a plain dict). This is fine for this use case:
  - A single Railway worker handles all webhooks (no horizontal scaling needed
    at 20-engineer scale).
  - If the process restarts, the worst case is an engineer has to type IN/OUT
    again, which is low impact.

If you later scale to multiple workers, replace this with a Redis-backed
state store using the same interface.

Conversation flow
-----------------
IDLE
  IN  → AWAITING_CHECKIN_LOCATION
  OUT → AWAITING_CHECKOUT_LOCATION

AWAITING_CHECKIN_LOCATION
  [location] → IDLE

AWAITING_CHECKOUT_LOCATION
  [location] → AWAITING_PROGRESS_REPORT   (checkout recorded)

AWAITING_PROGRESS_REPORT
  [any text] → AWAITING_MATERIAL_REQUEST  (progress report saved)

AWAITING_MATERIAL_REQUEST
  [any text] → IDLE                       (material request saved, or NONE)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ConversationStep(str, Enum):
    IDLE                       = "idle"
    AWAITING_CHECKIN_LOCATION  = "awaiting_checkin_location"
    AWAITING_CHECKOUT_LOCATION = "awaiting_checkout_location"
    AWAITING_PROGRESS_REPORT   = "awaiting_progress_report"
    AWAITING_MATERIAL_REQUEST  = "awaiting_material_request"


@dataclass
class ConversationState:
    step:          ConversationStep = ConversationStep.IDLE
    attendance_id: Optional[int]    = None   # set after checkout to link logs
    allocation_id: Optional[int]    = None   # set after checkout to link logs
    updated_at:    datetime         = field(default_factory=datetime.utcnow)


# whatsapp_number -> ConversationState
_state_store: dict = {}


def get_state(whatsapp_number: str) -> ConversationState:
    return _state_store.get(whatsapp_number, ConversationState())


def set_state(
    whatsapp_number: str,
    step: ConversationStep,
    attendance_id: Optional[int] = None,
    allocation_id: Optional[int] = None,
) -> None:
    _state_store[whatsapp_number] = ConversationState(
        step=step,
        attendance_id=attendance_id,
        allocation_id=allocation_id,
    )


def clear_state(whatsapp_number: str) -> None:
    _state_store.pop(whatsapp_number, None)
