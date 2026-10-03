#!/usr/bin/env python3
import re
from app.config import EXCLUSIONS_JOB_SEEKER_PATTERNS

text = """\u2728 Hiring Alert!
I'm looking for a Founder's Office Associate to work closely with me and the  team to help accelerate Dot - News, made new's growth and marketing.
... early-career candidates ..."""

text_lower = text.lower()

for i, pat in enumerate(EXCLUSIONS_JOB_SEEKER_PATTERNS):
    m = re.search(pat, text_lower, re.IGNORECASE)
    if m:
        print('Pattern', i, ':', pat)
        print('  Match:', m.group(0)[:80])
        print()