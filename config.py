"""Configurable thresholds and paths for the procurement auditing system.

All detection thresholds live here so they are not hard-coded in analysis modules.
Investigation-priority scores are NOT corruption probabilities.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "procurement_audit.db"
ERROR_LOG_PATH = OUTPUT_DIR / "parse_errors.log"

# Default TED bulk location (relative to workspace / override via env)
DEFAULT_TED_SOURCE = Path(r"C:\Users\anupa\Desktop\Coding\Python\M#\2026-08\08")

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
PROGRESS_EVERY = 100
MAX_FILES = None  # set to int for sampling during development
EXTRACT_ARCHIVES = True

# ---------------------------------------------------------------------------
# Peer / context engine
# ---------------------------------------------------------------------------
PEER_STRICTNESS = "medium"  # strict | medium | broad
PEER_MIN_GROUP_SIZE = 5
PEER_VALUE_BAND = 0.5  # medium: compare within +/- 50% of estimated value
PEER_YEAR_WINDOW = 1  # broad: nearby years

# ---------------------------------------------------------------------------
# Price analysis
# ---------------------------------------------------------------------------
PRICE_DEVIATION_PCT_MEDIUM = 15.0
PRICE_DEVIATION_PCT_HIGH = 25.0
PRICE_IQR_MULTIPLIER = 1.5

# ---------------------------------------------------------------------------
# Bidder analysis
# ---------------------------------------------------------------------------
LOW_BIDDER_COUNT = 2
SINGLE_BIDDER = 1
UNUSUAL_BID_MARGIN_PCT = 1.0  # margin under 1% of winning bid
REPEATED_WIN_RATE_HIGH = 0.7
REPEATED_WIN_MIN_TENDERS = 5

# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------
TOP_VENDOR_SHARE_HIGH = 0.5
HHI_HIGH = 0.25
BUYER_VENDOR_REPEAT_MIN = 5

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
COBID_MIN_SHARED = 3
HIGH_DEGREE_PERCENTILE = 95

# ---------------------------------------------------------------------------
# Rule severity weights (investigation priority contribution, 0-100 scale)
# ---------------------------------------------------------------------------
RULE_WEIGHTS = {
    "R1": 20,   # single bidder
    "R2": 12,   # very low bidder count
    "R3": 18,   # large deviation from peer price
    "R4": 15,   # repeated winner
    "R5": 12,   # high vendor concentration
    "R6": 10,   # repeated co-bidding pair
    "R7": 10,   # unusual bid margin
    "R8": 10,   # sudden behavioral change
    "R9": 12,   # repeated buyer-vendor relationship
    "R10": 10,  # unusual contract amount
    "R11": 8,   # multiple anomalies together (bonus applied in scoring)
}

# Score component caps before fusion
SCORE_CAPS = {
    "rule_score": 40,
    "statistical_score": 25,
    "behavior_score": 20,
    "network_score": 15,
    "ml_score": 15,
}

# Priority bands (investigation priority, NOT guilt)
PRIORITY_BANDS = {
    "low": (0, 50),
    "moderate": (50, 75),
    "high": (75, 90),
    "very_high": (90, 100),
}

# ---------------------------------------------------------------------------
# ML anomaly detection
# ---------------------------------------------------------------------------
ML_ENABLED = True
ML_CONTAMINATION = 0.05
ML_RANDOM_STATE = 42
ML_MIN_SAMPLES = 50

# ---------------------------------------------------------------------------
# False-positive / confidence reduction
# ---------------------------------------------------------------------------
SPECIALIZED_CPV_PREFIXES = ("33", "35", "72", "73", "85")  # medical, defence, IT, R&D, health
LOW_PEER_SIZE_THRESHOLD = 5
CONFIDENCE_PENALTY_LOW_PEERS = 0.5
CONFIDENCE_PENALTY_SPECIALIZED = 0.7

# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------
LEGAL_SUFFIXES = {
    "ltd": "limited",
    "ltd.": "limited",
    "limited": "limited",
    "llc": "llc",
    "l.l.c.": "llc",
    "inc": "inc",
    "inc.": "inc",
    "corp": "corp",
    "corporation": "corp",
    "plc": "plc",
    "gmbh": "gmbh",
    "srl": "srl",
    "s.r.l.": "srl",
    "s.r.l": "srl",
    "spa": "spa",
    "s.p.a.": "spa",
    "s.p.a": "spa",
    "sa": "sa",
    "s.a.": "sa",
    "bv": "bv",
    "nv": "nv",
    "oy": "oy",
    "ab": "ab",
    "pvt": "private",
    "pvt.": "private",
    "private": "private",
}

# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------
HIDE_CONTACT_FIELDS_BY_DEFAULT = True
