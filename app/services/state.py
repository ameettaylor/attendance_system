"""
In-memory conversation state machine.

When an engineer sends IN or OUT, the bot needs to wait for their next
message to be a location share.  This module tracks that pending state
per WhatsApp number so the webhook knows what to do with the next message.

State is stored in memory (a plain dict).  This is fine for this use case:
  - A single Railway worker handles all webhooks (no horizontal scaling needed
    at 20-engineer scale).
  - If the process restarts, the worst case is an engineer has to type IN/OUT
    again, which is low impact.

If you later scale to multiple workers, replace this with a Redis-backed
state store using the same interface.
"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

class ConversationStep(str, Enum):
    IDLE              = "idle"
    AWAITING_CHECKIN_LOCATION  = "awaiting_checkin_location"
    AWAITING_CHECKOUT_LOCATION = "awaiting_checkout_location"


@dataclass
class ConversationState:
    step: ConversationStep = ConversationStep.IDLE
    updated_at: datetime = field(default_factory=datetime.utcnow)


# whatsapp_number -> ConversationState
_state_store: dict = {}


def get_state(whatsapp_number: str) -> ConversationState:
    return _state_store.get(whatsapp_number, ConversationState())


def set_state(whatsapp_number: str, step: ConversationStep) -> None:
    _state_store[whatsapp_number] = ConversationState(step=step)


def clear_state(whatsapp_number: str) -> None:
    _state_store.pop(whatsapp_number, None)
