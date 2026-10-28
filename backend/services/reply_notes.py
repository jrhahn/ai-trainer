"""What the app itself says under a coach reply (ai-trainer-ops#262).

The router and the chat service wrote these notes into the reply's own text, in
two styles ("(Hinweis: …)" and "Hinweis: …") and with two language detections.
The reply was stored with them and replayed to the model as the coach's own
earlier words, and the language check read them as part of the reply. A note is
data now: ``{"kind", "text"}`` beside the reply, stored in
``ChatMessage.notes_json``, shown under the reply, and never part of the history
the model reads.
"""

from __future__ import annotations

from typing import TypedDict


class ReplyNote(TypedDict):
    kind: str
    text: str


# From the checks on the coach's own reply (``ai_service._reply_findings``).
UNTRACED_NUMBERS = "untraced_numbers"
PLAN_CHANGE_PRIORS = "plan_change_priors"
STANDING_PRIORS = "standing_priors"
UNCARRIED_CLAIM = "uncarried_claim"
FTP_CLAIM = "ftp_claim"
FTP_STATED = "ftp_stated"
LANGUAGE = "language"
PROMISE = "promise"
# From what the router did around the reply.
UNRESOLVED_DATES = "unresolved_dates"
CONSTRAINT_OVERRIDE = "constraint_override"
SESSION_CAP = "session_cap"
RULED_OUT_DAYS = "ruled_out_days"
ILLNESS_RETURN = "illness_return"
CONSTRAINT_LIFT = "constraint_lift"
LOCATION = "location"


def note(kind: str, text: str) -> ReplyNote:
    return {"kind": kind, "text": text}
