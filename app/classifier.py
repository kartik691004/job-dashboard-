import re
from app.config import ROLE_CATEGORIES, HIRING_SIGNALS, EXCLUSIONS, PROXIMITY_CHAR_LIMIT
from app.models import RawPost, ClassifiedPost

class DeterministicClassifier:
    def classify(self, post: RawPost) -> ClassifiedPost:
        text_lower = post.text.lower()
        
        # 1. Check Exclusions
        for ex in EXCLUSIONS:
            if ex in text_lower:
                return self._create_invalid(post, f"Matched exclusion: '{ex}'")
                
        # 2. Find Role Keywords (across every role category)
        matched_roles = []
        for category, keywords in ROLE_CATEGORIES.items():
            for role in keywords:
                if role in text_lower:
                    matched_roles.append((category, role))
                
        if not matched_roles:
            return self._create_invalid(post, "No role keywords found")
            
        # 3. Find Hiring Signals
        matched_signals = []
        for sig in HIRING_SIGNALS:
            if sig in text_lower:
                matched_signals.append(sig)
                
        if not matched_signals:
            return self._create_invalid(post, "No hiring intent signals found")
            
        # 4. Proximity Check
        min_distance = float('inf')
        best_category = ""
        best_role = ""
        best_signal = ""
        
        for category, role in matched_roles:
            for role_match in re.finditer(re.escape(role), text_lower):
                role_start = role_match.start()
                role_end = role_match.end()
                
                for sig in matched_signals:
                    for sig_match in re.finditer(re.escape(sig), text_lower):
                        sig_start = sig_match.start()
                        sig_end = sig_match.end()
                        
                        # Calculate distance
                        if role_end < sig_start:
                            dist = sig_start - role_end
                        elif sig_end < role_start:
                            dist = role_start - sig_end
                        else:
                            dist = 0 # Overlapping
                            
                        if dist < min_distance:
                            min_distance = dist
                            best_category = category
                            best_role = role
                            best_signal = sig

        if min_distance > PROXIMITY_CHAR_LIMIT:
            return self._create_invalid(post, f"Keywords found but too far apart (Distance: {min_distance} chars)")
            
        # 5. Determine Confidence
        confidence = 1.0 if min_distance <= 100 else 0.8
        
        # 6. Determine Employment Type
        emp_type = "Full-time"
        if "intern" in text_lower or "internship" in text_lower:
            emp_type = "Internship"
            
        snippet = post.text[:150] + "..." if len(post.text) > 150 else post.text
        
        return ClassifiedPost(
            **post.model_dump(),
            role_category=best_category,
            employment_type=emp_type,
            detected_role_title="Unclear",
            matched_role_keywords=best_role,
            hiring_intent_signals=best_signal,
            confidence=confidence,
            post_snippet=snippet,
            classification_reason="Deterministic Match",
            is_valid=True
        )
        
    def _create_invalid(self, post: RawPost, reason: str) -> ClassifiedPost:
        return ClassifiedPost(
            **post.model_dump(),
            role_category="N/A",
            employment_type="N/A",
            detected_role_title="N/A",
            matched_role_keywords="N/A",
            hiring_intent_signals="N/A",
            confidence=0.0,
            post_snippet=post.text[:100] + "..." if len(post.text) > 100 else post.text,
            classification_reason=reason,
            is_valid=False
        )
