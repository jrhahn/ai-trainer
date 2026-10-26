"""The physiological systems a session works and the coarse gains it can bring (ai-trainer-ops#304).

The ROI recommendation ranks by them, and the training utility scores by them.
They live apart from both, so neither has to import the other to read them.
"""

from __future__ import annotations

# Physiological systems a recommendation can emphasise.
SYSTEM_THRESHOLD = "threshold"
SYSTEM_VO2MAX = "vo2max"
SYSTEM_ENDURANCE = "endurance"
SYSTEM_ANAEROBIC = "anaerobic"

# The running systems (#718), named separately rather than reusing the four
# above. "Threshold" in this module has always meant watts at FTP; a running
# threshold session is a pace against Critical Speed, and letting one identifier
# mean both is the conflation #579, #712 and #716 each spent an issue undoing.
# Distinct names also mean a weekly emphasis can hold two threshold sessions —
# one on the bike and one on foot — without them collapsing into each other.
SYSTEM_RUN_THRESHOLD = "run_threshold"
SYSTEM_RUN_VO2MAX = "run_vo2max"
SYSTEM_RUN_ENDURANCE = "run_endurance"

# Expected-gain buckets, coarse on purpose — the model is not precise enough to
# claim watt-level returns, so we speak in relative return-on-investment.
GAIN_LARGE = "large"
GAIN_MODERATE = "moderate"
GAIN_SMALL = "small"
GAIN_MAINTENANCE = "maintenance"

# Public: #602's day board ranks the same buckets when a kind of day could be
# delivered as either of two systems.
GAIN_RANK = {GAIN_LARGE: 3, GAIN_MODERATE: 2, GAIN_SMALL: 1, GAIN_MAINTENANCE: 0}
