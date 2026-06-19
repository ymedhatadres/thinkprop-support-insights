"""Clean ticket descriptions for the qualitative reading view.

Real complaint bodies are buried under email banners, greetings, signatures,
disclaimers, and decorative whitespace. This module strips that noise and
extracts the actual complaint text. It also flags structured form-submission
templates (e.g. "Appointment Type: Transfer of Interest") which are not
free-text complaints.
"""

from __future__ import annotations

import re

import pandas as pd

# Cut everything from these markers onward — signatures and disclaimers.
_SIGNATURE_CUTOFFS = [
    r"\n\s*regards\b",
    r"\n\s*best regards\b",
    r"\n\s*kind regards\b",
    r"\n\s*warm regards\b",
    r"\n\s*thanks\b",
    r"\n\s*thank you\b",
    r"\n\s*thanks (?:&|and) regards\b",
    r"\n\s*sincerely\b",
    r"\n\s*sent from my\b",
    r"\n\s*get outlook for\b",
    r"\bdisclaimer\b\s*\n",
    r"\*{5,}",
    r"caution\s*:\s*this email originated",
]
_SIG_RE = re.compile("|".join(_SIGNATURE_CUTOFFS), re.IGNORECASE)

# Banner markers that wrap external-sender warnings.
_BANNER_RE = re.compile(
    r"ZjQcmQRYFpfpt\w*",
    re.IGNORECASE,
)
_EXT_SENDER_RE = re.compile(
    r"this message is from an external sender[^\n]*",
    re.IGNORECASE,
)
# Forwarded / quoted email headers (Outlook & Gmail styles).
_FWD_HEADER_RE = re.compile(
    r"_{5,}.*?(?=\n|$)|"
    r"\bfrom:\s+.+?\bsent:\s+.+?\bto:\s+[^\n]*|"
    r"\bon\s+\w+,?\s+\w+\s+\d{1,2},?\s+\d{4}.*?wrote:",
    re.IGNORECASE | re.DOTALL,
)

# Zero-width chars and other invisible punctuation.
_ZWJ_RE = re.compile(r"[​-‏‪-‮⁠﻿]+")

# Common greetings to strip (keep the rest).
_GREETING_RE = re.compile(
    r"^\s*(?:dear\s+(?:adgm\s+)?(?:support\s+)?(?:team|sir(?:/|\s+or\s+)?madam|sir|madam|all|valued\s+customer)[,!.: ]*\n*"
    r"|hi(?:\s+team)?[,!.: ]*\n*"
    r"|hello(?:\s+team)?[,!.: ]*\n*"
    r"|good\s+(?:morning|afternoon|day|evening)[,!.: ]*\n*"
    r"|greetings[,!.: ]*\n*"
    r"|to whom it may concern[,!.: ]*\n*)+",
    re.IGNORECASE,
)

# Strip leftover punctuation/whitespace at the very start.
_LEADING_NOISE_RE = re.compile(r"^[\s,.;:!?\-*]+")

# Form-template detector: structured booking submissions.
_FORM_TEMPLATE_RE = re.compile(
    r"(?:"
    r"please book (?:an? )?appointment for the below"
    r"|appointment type\s*:\s*"
    r"|customer name\s*:\s*.+\n.*mobile number\s*:\s*"
    r")",
    re.IGNORECASE,
)

# Multiple whitespace / newlines normalisation.
_MULTI_WS_RE = re.compile(r"[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def clean_description(text: str | float | None) -> str:
    """Return a cleaned, human-readable version of a ticket description.

    Returns "" if input is null. Output is safe to display verbatim.
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    s = str(text)

    s = _ZWJ_RE.sub("", s)
    s = _BANNER_RE.sub(" ", s)
    s = _EXT_SENDER_RE.sub(" ", s)
    s = _FWD_HEADER_RE.sub(" ", s)

    m = _SIG_RE.search(s)
    if m:
        s = s[: m.start()]

    s = _GREETING_RE.sub("", s, count=1)
    s = _LEADING_NOISE_RE.sub("", s)
    s = _MULTI_WS_RE.sub(" ", s)
    s = _MULTI_NL_RE.sub("\n\n", s)
    return s.strip()


def is_form_template(text: str | float | None) -> bool:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return False
    return bool(_FORM_TEMPLATE_RE.search(str(text)))


def add_clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add `description_clean` and `is_form_template` columns to df."""
    out = df.copy()
    out["description_clean"] = out["description_text"].map(clean_description)
    out["is_form_template"] = out["description_text"].map(is_form_template)
    return out


def extract_sentences(text: str, keywords: list[str], max_sentences: int = 2) -> str:
    """Extract sentences from `text` that mention any of `keywords`.

    Returns up to `max_sentences` joined by spaces. Falls back to the first
    chunk of `text` if no sentence matches.
    """
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10]
    if not keywords:
        return " ".join(parts[:max_sentences])
    kw_re = re.compile(
        "|".join(re.escape(k) for k in keywords), re.IGNORECASE
    )
    hits = [p for p in parts if kw_re.search(p)]
    if hits:
        return " ".join(hits[:max_sentences])
    return " ".join(parts[:max_sentences])
