"""
geo.py — deterministic "is this post about a job in India?" detection.

Why this exists: the Apify actor has NO location input field (its entire schema is
keywords / max_posts / sort_by / date_filter), so geography cannot be filtered at the
source. Before this module, 82 of 101 accepted leads were outside India.

Two signals, in priority order:

1. The LinkedIn job-card location (`content.description`, e.g. "Noida, Uttar Pradesh,
   India (On-site)"). AUTHORITATIVE — when present it decides on its own and the post
   text is not consulted. Present on only ~18% of posts, hence signal 2.
2. The post text, scanned for India place names and Indian-market money/HR idioms.

Every term below was measured against a real 330-post corpus
(data/analysis/2026-08-23_geo_projection.json) rather than guessed. Two candidates that
look obvious were REJECTED on that evidence:

  * "punjab" — fired twice, both times on "Lahore, Punjab, Pakistan". Punjab spans the
    India/Pakistan border and is never usable alone.
  * ".in" domain matching — fired 80 times, every single one on "lnkd.in", LinkedIn's
    own URL shortener.

"Salem" and "Kochi" are likewise omitted: Salem is Oregon/Massachusetts far more often
than Tamil Nadu, and Kochi collides with Kōchi, Japan.

Extended 2026-08-23 against a second corpus (the 393-row FTB internships reference
sheet), which exposed two gaps:

  * Tier-2 cities were missing entirely — "Ranchi" was read as non-India. The cities
    added below each have at least one verified India-context match in a saved corpus.
  * "Pvt. Ltd." / "Private Limited" appeared in 34 records, every one Indian. It is the
    only signal on remote roles that name no city at all. It is NOT decisive though:
    Pakistan, Sri Lanka, Nepal and Bangladesh use the same company form, so it is
    applied last and only when the text mentions no South-Asian neighbour.

"GST" was measured (10 records, all Indian tax context) and still REJECTED: Australia,
Canada, New Zealand, Singapore and Malaysia all levy a GST, and the corpus cannot show
a collision that our India-skewed search keywords never surfaced.
"""
import re
from typing import Tuple

# ── Traps: strings containing "indian" that are NOT in India ──────────────────
# \bindian?\b already refuses "Indiana" and "Indianapolis" (the word boundary fails on
# the trailing letters), so only multi-word traps need handling. These are blanked out
# of the text BEFORE matching. "Indian Army"/"Indian Railways" are deliberately NOT
# trapped — those organisations genuinely are in India.
_TRAPS = re.compile(
    r"\b(?:"
    r"indian\s+ocean|british\s+indian\s+ocean\s+territory|west\s+indies|"
    r"american\s+indian|native\s+indian|indian\s+wells|indian\s+river|indian\s+head|"
    r"indian\s+trail|indian\s+springs|indian\s+hills"
    r")\b",
    re.I,
)

# ── Decisive: a single match proves the Indian market ─────────────────────────
# Currency and Indian-HR idioms. Nobody quotes a salary in lakhs/LPA/rupees for a job
# outside India.
_DECISIVE = re.compile(
    r"₹|\bINR\b|\bRs\.?\s*\d|\bLPA\b|\blakhs?\b|\bcrores?\b|\+91[\s-]?\d|"
    r"\bPAN[\s-]India\b|\bIIT\b|\bIIM\b|\bBITS\s+Pilani\b",
    re.I,
)

# ── Place names that are effectively India-unique ─────────────────────────────
# Deliberately excludes: punjab (Pakistan), salem (US), kochi (Japan), hyderabad and
# delhi are included but are ALWAYS subject to the foreign-country guard below, because
# Hyderabad, Sindh (Pakistan) and Delhi, Ontario (Canada) both exist.
_PLACES = re.compile(
    r"\b(?:"
    r"india|indias|indian|bharat|"
    r"bengaluru|bangalore|mumbai|bombay|delhi|new\s+delhi|ncr|noida|gurgaon|gurugram|"
    r"hyderabad|pune|chennai|madras|kolkata|calcutta|ahmedabad|jaipur|indore|"
    r"coimbatore|chandigarh|bhubaneswar|visakhapatnam|vizag|nagpur|surat|vadodara|"
    r"lucknow|kanpur|bhopal|thane|mysuru|mysore|trivandrum|thiruvananthapuram|"
    # Tier-2 additions, each with a verified India-context match in a saved corpus:
    r"ranchi|dehradun|guwahati|amritsar|agra|ghaziabad|navi\s+mumbai|raipur|"
    r"karnataka|maharashtra|telangana|tamil\s+nadu|kerala|gujarat|rajasthan|haryana|"
    r"uttar\s+pradesh|west\s+bengal|andhra\s+pradesh|madhya\s+pradesh|odisha|"
    r"assam|chhattisgarh"
    r")\b",
    re.I,
)

# ── Indian company form: last-resort signal ───────────────────────────────────
# "Pvt. Ltd." is the Indian Companies Act designation and is the ONLY India signal on
# remote roles that name no city (34 corpus records, all Indian). It is checked last and
# guarded, because it is South-Asian rather than Indian: Pakistan, Sri Lanka, Nepal and
# Bangladesh register companies the same way.
_COMPANY_FORM = re.compile(r"\bpvt\.?\s*ltd\b|\bprivate\s+limited\b", re.I)

# Narrow neighbour guard, applied ONLY to the ambiguous _COMPANY_FORM path. Deliberately
# not applied to the ₹/LPA/city paths, where an Indian post may legitimately mention a
# neighbouring market without being a job there.
_NEIGHBOURS = re.compile(
    r"\b(?:pakistan|bangladesh|sri\s+lanka|nepal|bhutan|"
    r"lahore|karachi|islamabad|rawalpindi|dhaka|chittagong|colombo|kathmandu)\b",
    re.I,
)

# ── Foreign-country guard ─────────────────────────────────────────────────────
# Applied ONLY to the authoritative job-card location, never to free text: a post can
# legitimately mention a US head office while hiring in Bangalore, but a job card that
# says "Pakistan" is a job in Pakistan. This is what stops "Hyderabad, Sindh, Pakistan"
# and "Lahore, Punjab, Pakistan" from being read as Indian cities.
_FOREIGN = re.compile(
    r"\b(?:"
    r"pakistan|bangladesh|sri\s+lanka|nepal|bhutan|maldives|afghanistan|"
    r"united\s+states|u\.?s\.?a|usa|canada|mexico|brazil|argentina|colombia|chile|"
    r"latin\s+america|united\s+kingdom|u\.?k\.?|england|scotland|ireland|"
    r"germany|france|spain|italy|portugal|netherlands|belgium|switzerland|austria|"
    r"poland|czech|hungary|romania|bulgaria|greece|sweden|norway|denmark|finland|"
    r"t[üu]rkiye|turkey|israel|egypt|nigeria|kenya|south\s+africa|ghana|morocco|"
    r"uae|united\s+arab\s+emirates|dubai|abu\s+dhabi|saudi|qatar|bahrain|kuwait|oman|"
    r"china|japan|korea|vietnam|thailand|indonesia|malaysia|singapore|philippines|"
    r"australia|new\s+zealand|liechtenstein|luxembourg|estonia|lithuania|latvia"
    r")\b",
    re.I,
)


def _clean(text: str) -> str:
    """Blank out trap phrases so they cannot be seen as an India match."""
    return _TRAPS.sub(" ", text or "")


def is_india(text: str, location: str = "") -> Tuple[bool, str]:
    """Return (is_india, human-readable evidence).

    The evidence string is written into the Sheet's existing 'Classification Reason'
    column, so a reviewer can see *why* a row was kept without opening the post. No new
    column is introduced — the 15-column header schema is fixed.
    """
    loc = (location or "").strip()

    # 1. Authoritative job-card location decides alone when present.
    if loc:
        cleaned_loc = _clean(loc)
        if _FOREIGN.search(cleaned_loc):
            return False, f"job location outside India: {loc}"
        m = _PLACES.search(cleaned_loc)
        if m:
            return True, f"job location: {loc}"
        return False, f"job location not recognised as India: {loc}"

    # 2. Fall back to the post text.
    body = _clean(text or "")

    m = _DECISIVE.search(body)
    if m:
        return True, f"Indian-market marker in text: '{m.group(0).strip()}'"

    m = _PLACES.search(body)
    if m:
        return True, f"India place name in text: '{m.group(0).strip()}'"

    # 3. Last resort: an Indian company form, but only if no South-Asian neighbour is
    #    named anywhere in the text. This is what rescues remote roles at Indian private
    #    limiteds that never mention a city.
    m = _COMPANY_FORM.search(body)
    if m and not _NEIGHBOURS.search(body):
        return True, f"Indian company form in text: '{m.group(0).strip()}'"

    return False, "no India signal in post text or job location"
