"""
tools/query_strategy.py

Targeted LinkedIn search-query matrix for the historical/recall improvement.

SCOPE: This module ONLY generates the QUERY STRATEGY. It does NOT touch the
verifier, classifier, India gate, hard gates, or any production acceptance
logic. The production pipeline (app/) is unchanged; this is a candidate
replacement for app/config.SEARCH_QUERIES, proposed for review.

Design goals (per brief):
  * Recall genuine CURRENT Indian Founder's Office / Chief of Staff vacancies.
  * Strictly exclude internships, aggregators, advice, job-seekers,
    congratulatory, generic "founder" content, historical/non-India jobs.
  * Small, high-quality matrix (no combinatorial explosion) to control Apify cost.
  * Every query combines a ROLE with a genuine hiring signal and/or an India
    anchor, so the actor already pre-filters toward real vacancies.

The actor (datadoping/linkedin-posts-search-scraper) receives each query as a
quoted phrase combo and enforces sort_by=date_posted + date_filter=past-24h,
so recency is preserved end-to-end.
"""
import re

# --- Component term lists (used by the benchmark matcher) -------------------
ROLE_TERMS = [
    "founder's office", "founders office", "founder office",
    "founder's associate", "founder associate", "office of the founder",
    "chief of staff",
]
HIRING_TERMS = [
    "hiring", "we're hiring", "we are hiring", "now hiring", "looking for",
    "join our team", "join us", "applications open", "apply",
    "send your resume", "seeking candidates",
]
INDIA_TERMS = [
    "india", "bangalore", "bengaluru", "mumbai", "delhi", "new delhi",
    "gurgaon", "gurugram", "hyderabad", "pune", "chennai", "noida",
    "ahmedabad", "jaipur", "kolkata", "remote - india", "remote india",
]

# --- Query matrix ----------------------------------------------------------
# Each entry is documented so the benchmark can be audited by a human.
QUERY_MATRIX = [
    # --- Tier A: Role + "hiring" (broad, high FO/CoS vacancy recall) --------
    {
        "id": "A1", "query": '"founder\'s office" hiring',
        "role": "Founder's Office", "signal": "hiring", "india_anchor": None,
        "why": "Broadest genuine-vacancy recall for Founder's Office roles; 'hiring' filters out pure founder/company content.",
        "expected_noise": "Moderate — some non-vacancy FO thought-leadership slips through; verifier rejects it.",
        "india_relevance": "Not enforced in query; India gate in classifier handles it.",
        "role_coverage": "Founder's Office (all variants)",
    },
    {
        "id": "A2", "query": '"founders office" hiring',
        "role": "Founder's Office", "signal": "hiring", "india_anchor": None,
        "why": "Alternate spelling/plural catches posts the apostrophe variant misses.",
        "expected_noise": "Low-moderate.",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Founder's Office",
    },
    {
        "id": "A3", "query": '"founder\'s office associate" hiring',
        "role": "Founder's Office", "signal": "hiring", "india_anchor": None,
        "why": "Targets the most common FO vacancy title (Associate), raising precision within FO.",
        "expected_noise": "Low.",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Founder's Office Associate",
    },
    {
        "id": "A4", "query": '"founder\'s office executive" hiring',
        "role": "Founder's Office", "signal": "hiring", "india_anchor": None,
        "why": "Targets FO Executive vacancies specifically.",
        "expected_noise": "Low.",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Founder's Office Executive",
    },
    {
        "id": "A5", "query": '"chief of staff" hiring',
        "role": "Chief of Staff", "signal": "hiring", "india_anchor": None,
        "why": "Broadest CoS vacancy recall.",
        "expected_noise": "Moderate — CoS is a noisier term (Ops/COO-adjacent content).",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Chief of Staff",
    },
    # --- Tier B: Role + explicit current-hiring phrase (lower noise) ----------
    {
        "id": "B1", "query": '"founder\'s office" "we\'re hiring"',
        "role": "Founder's Office", "signal": "we're hiring", "india_anchor": None,
        "why": "Strong first-person current-hiring signal; drops advice/historical noise.",
        "expected_noise": "Low.",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Founder's Office",
    },
    {
        "id": "B2", "query": '"chief of staff" "we\'re hiring"',
        "role": "Chief of Staff", "signal": "we're hiring", "india_anchor": None,
        "why": "Strong current-hiring CoS signal.",
        "expected_noise": "Low.",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Chief of Staff",
    },
    {
        "id": "B3", "query": '"founder\'s office" "looking for"',
        "role": "Founder's Office", "signal": "looking for", "india_anchor": None,
        "why": "'looking for' is genuine-vacancy phrasing used by hiring managers.",
        "expected_noise": "Low-moderate (some 'looking for' cofounders/network).",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Founder's Office",
    },
    {
        "id": "B4", "query": '"chief of staff" "looking for"',
        "role": "Chief of Staff", "signal": "looking for", "india_anchor": None,
        "why": "Genuine-vacancy CoS phrasing.",
        "expected_noise": "Low-moderate.",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Chief of Staff",
    },
    # --- Tier C: Role + India anchor (enforces geography in-query) ------------
    {
        "id": "C1", "query": '"founder\'s office" India',
        "role": "Founder's Office", "signal": None, "india_anchor": "India",
        "why": "Pushes India into the search so non-India FO posts are pre-filtered by the actor.",
        "expected_noise": "Low (may include India FO advice, rejected by verifier).",
        "india_relevance": "Explicit India in query.",
        "role_coverage": "Founder's Office",
    },
    {
        "id": "C2", "query": '"chief of staff" India',
        "role": "Chief of Staff", "signal": None, "india_anchor": "India",
        "why": "India-anchored CoS search.",
        "expected_noise": "Low.",
        "india_relevance": "Explicit India in query.",
        "role_coverage": "Chief of Staff",
    },
    # --- Tier D: Role + high-yield India metros (precise geography) -----------
    {
        "id": "D1", "query": '"chief of staff" Bangalore',
        "role": "Chief of Staff", "signal": None, "india_anchor": "Bangalore",
        "why": "Bangalore is the largest Indian startup hub; high CoS vacancy density.",
        "expected_noise": "Low.",
        "india_relevance": "Bangalore, India.",
        "role_coverage": "Chief of Staff",
    },
    {
        "id": "D2", "query": '"founder\'s office" Mumbai',
        "role": "Founder's Office", "signal": None, "india_anchor": "Mumbai",
        "why": "Mumbai startup/VC ecosystem; strong FO demand.",
        "expected_noise": "Low.",
        "india_relevance": "Mumbai, India.",
        "role_coverage": "Founder's Office",
    },
    {
        "id": "D3", "query": '"chief of staff" Delhi',
        "role": "Chief of Staff", "signal": None, "india_anchor": "Delhi",
        "why": "Delhi/NCR CoS openings.",
        "expected_noise": "Low.",
        "india_relevance": "Delhi, India.",
        "role_coverage": "Chief of Staff",
    },
    # --- Tier E: Role + apply CTA (hiring-intent) ----------------------------
    {
        "id": "E1", "query": '"chief of staff" apply',
        "role": "Chief of Staff", "signal": "apply", "india_anchor": None,
        "why": "'apply' CTA strongly correlates with live job posts.",
        "expected_noise": "Low.",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Chief of Staff",
    },
    {
        "id": "E2", "query": '"founder\'s office" apply',
        "role": "Founder's Office", "signal": "apply", "india_anchor": None,
        "why": "'apply' CTA for FO openings.",
        "expected_noise": "Low.",
        "india_relevance": "Classifier India gate.",
        "role_coverage": "Founder's Office",
    },
]


def build_query_matrix():
    """Return the documented query matrix (list of dicts). Pure data."""
    return [dict(q) for q in QUERY_MATRIX]


def _norm(s):
    return (s or "").lower().replace("\u2019", "'").replace("\u2018", "'")


def _tokenize(query):
    """Split a query into tokens, keeping quoted phrases intact."""
    tokens, i = [], 0
    while i < len(query):
        c = query[i]
        if c == '"':
            j = query.find('"', i + 1)
            if j == -1:
                tokens.append(query[i + 1:])
                break
            tokens.append(query[i + 1:j])
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < len(query) and not query[j].isspace():
                j += 1
            tokens.append(query[i:j])
            i = j
    return tokens


def _role_in_text(t):
    return any(_norm(r) in t for r in ROLE_TERMS)


def post_matches_query(text, query):
    """Does a scraped post TEXT satisfy a benchmark query's intent?

    Requires at least one ROLE phrase AND every HIRING/INDIA token from the
    query to be present in the text. Quoted phrases and unquoted anchor words
    (e.g. `"chief of staff" India`) are both honoured. Used for per-query
    attribution in the benchmark without one actor run per query.
    """
    t = _norm(text)
    tokens = [_norm(tok) for tok in _tokenize(query)]
    role_set = [_norm(r) for r in ROLE_TERMS]
    signal_set = [_norm(h) for h in HIRING_TERMS]
    india_set = [_norm(i) for i in INDIA_TERMS]

    role_tok = [tok for tok in tokens if tok in role_set]
    signal_tok = [tok for tok in tokens if tok in signal_set]
    india_tok = [tok for tok in tokens if tok in india_set]

    if not role_tok:
        return False
    if not all(tok in t for tok in role_tok):
        return False
    if signal_tok and not all(tok in t for tok in signal_tok):
        return False
    if india_tok and not all(tok in t for tok in india_tok):
        return False
    return True
