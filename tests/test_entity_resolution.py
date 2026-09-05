"""Unit tests for the pure entity-resolution functions (no DB required)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services import entity_resolution as er

PERSONS = [
    {"person_id": "P1", "full_name": "Rahul Kumar Sharma", "aliases": "R. Sharma|Rahul Sharma",
     "address": "Flat 3B, Shrivardhan Apartments"},
    {"person_id": "P2", "full_name": "Priya Nair", "aliases": "",
     "address": "Flat 3B, Shrivardhan Apartments"},
    {"person_id": "P3", "full_name": "Rohit Sharma", "aliases": "",
     "address": "12 Some Other Road"},
]

fails = []
def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")

# --- phone normalization ---------------------------------------------------
for raw in ["+91 90000 14417", "9000014417", "09000014417", "+919000014417",
            "91-90000-14417", "0091 9000014417", "(9000) 014417"]:
    check(f"normalize_phone({raw!r})", er.normalize_phone(raw), "+919000014417")
check("normalize_phone(None)", er.normalize_phone(None), None)
check("normalize_phone('')", er.normalize_phone(""), None)
check("normalize_phone('abc')", er.normalize_phone("abc"), None)
check("is_plausible ok", er.is_plausible_phone("+919000014417"), True)
check("is_plausible short", er.is_plausible_phone("+9112345"), False)

# --- name normalization ----------------------------------------------------
check("honorific stripped", er.normalize_name("Smt. Sulabha  Chaudhari"), "sulabha chaudhari")
check("punctuation stripped", er.normalize_name("R. Nagvekar"), "r nagvekar")
check("empty", er.normalize_name(None), "")
check("variants", er.name_variants("Rahul Kumar Sharma"),
      {"rahul kumar sharma", "rahul sharma", "r sharma", "r k sharma"})

# --- alias index -----------------------------------------------------------
idx = er.build_alias_index(PERSONS)
check("canonical name", er.resolve_alias("Rahul Kumar Sharma", idx), "P1")
check("explicit alias", er.resolve_alias("R. Sharma", idx), "P1")
check("explicit alias 2", er.resolve_alias("Rahul Sharma", idx), "P1")
check("case/punct insensitive", er.resolve_alias("r  sharma", idx), "P1")
check("other person", er.resolve_alias("Rohit Sharma", idx), "P3")
check("unknown name", er.resolve_alias("Nobody Here", idx), None)
check("unknown is NO_MATCH", er.resolve_alias_verbose("Nobody Here", idx)[1], "NO_MATCH")
check("explicit beats generated", er.resolve_alias_verbose("R. Sharma", idx), ("P1", "MATCH"))

# ambiguity: two people whose generated variants collide must not resolve
amb = er.build_alias_index([
    {"person_id": "A", "full_name": "Sunil Mehta", "aliases": ""},
    {"person_id": "B", "full_name": "Sanjay Mehta", "aliases": ""},
])
check("colliding initial form is ambiguous", er.resolve_alias("S. Mehta", amb), None)
check("ambiguous status", er.resolve_alias_verbose("S. Mehta", amb)[1], "AMBIGUOUS")
check("full names still resolve", er.resolve_alias("Sunil Mehta", amb), "A")

# --- free-text mention scan ------------------------------------------------
hits = er.find_alias_mentions(
    "The complainant states that one R. Sharma was seen with Rohit Sharma near the market.", idx)
check("mention scan finds both", {p for _, p in hits}, {"P1", "P3"})
check("no false hit on empty text", er.find_alias_mentions("nothing here", idx), [])

# --- SIM mismatch: association, never a merge ------------------------------
names = {"P1": "Rahul Kumar Sharma", "P2": "Priya Nair", "P3": "Rohit Sharma"}
same = er.detect_sim_name_mismatch(
    {"phone_id": "PH1", "number": "+919000000001", "owner_person_id": "P1",
     "sim_registered_name": "Rahul Kumar Sharma"}, names, idx)
check("matching name -> no flag", same, None)
alias_form = er.detect_sim_name_mismatch(
    {"phone_id": "PH2", "number": "+919000000002", "owner_person_id": "P1",
     "sim_registered_name": "R. Sharma"}, names, idx)
check("alias form of same person -> no flag", alias_form, None)
third = er.detect_sim_name_mismatch(
    {"phone_id": "PH3", "number": "+919000000003", "owner_person_id": "P1",
     "sim_registered_name": "Priya Nair"}, names, idx)
check("third-party registration flagged", third["kind"], "SIM_REGISTERED_TO_THIRD_PARTY")
check("third-party resolves to person", third["registered_person_id"], "P2")
check("owner unchanged (no merge)", third["actual_owner_person_id"], "P1")
unknown = er.detect_sim_name_mismatch(
    {"phone_id": "PH4", "number": "+919000000004", "owner_person_id": "P1",
     "sim_registered_name": "Some Walk-In Name"}, names, idx)
check("unknown registrant flagged separately", unknown["kind"], "SIM_REGISTERED_TO_UNKNOWN_NAME")
check("unknown registrant has no person id", unknown["registered_person_id"], None)

# --- shared address: soft candidate only -----------------------------------
cands = er.shared_address_candidates(PERSONS)
check("one shared-address pair", len(cands), 1)
check("pair is P1/P2", (cands[0]["person_a"], cands[0]["person_b"]), ("P1", "P2"))
check("confidence is low", cands[0]["confidence"] <= 0.25, True)
check("requires review", cands[0]["requires_human_review"], True)
check("address normalization drops 'flat'",
      "flat" in er.normalize_address("Flat 3B, Shrivardhan Apartments"), False)

# ---------------------------------------------------------------------------
if fails:
    print(f"FAILED ({len(fails)}):")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("entity_resolution: all assertions passed")
