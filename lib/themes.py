"""Noise filtering and issue classification for ThinkProp support tickets.

Unlike AccessRP (which needed a bilingual keyword classifier), ThinkProp's
Freshdesk export already carries an agent-labelled inquiry taxonomy in
`custom_fields.cf_inquiry_type328109` — 32 clean issue types. We use that field
directly as the primary theme. A light keyword fallback only kicks in for the
handful of tickets agents left unlabelled.

Adds three columns used by the report:
  is_noise       - test / auto-generated tickets to exclude
  theme_primary  - the inquiry type (or keyword-inferred / "Other")
  themes_list    - single-element list wrapping theme_primary (the report's
                   downstream code treats issues as a multi-label list)
"""

from __future__ import annotations

import re

import pandas as pd

INQUIRY_COL = "custom_fields.cf_inquiry_type328109"
SERVICE_COL = "custom_fields.cf_service"

NOISE_SERVICE = "Ads Test Tickets and Auto-Emails"
NOISE_INQUIRY = "Ads Test Tickets and Auto-Emails"
NOISE_TYPE = "Ads Test Tickets and Auto-Emails"

UNCLASSIFIED = "Other / unclassified"

# Subjects that are obviously automation, regardless of service.
NOISE_SUBJECT_PATTERNS = [
    r"^your message couldn'?t be delivered",
    r"undeliverable",
    r"delivery (?:has )?failed",
    r"mail delivery failed",
    r"out of office",
    r"automatic reply",
    r"^test$",
    r"^\s*$",
]
_NOISE_RE = re.compile("|".join(NOISE_SUBJECT_PATTERNS), re.IGNORECASE)

# Keyword fallback for unlabelled tickets. Order matters — first match wins.
# Each maps to one of the canonical inquiry types so it folds cleanly into the
# pain-category mapping in build_report.py. Bilingual (EN + AR) because ~50% of
# ThinkProp tickets are written in Arabic.
_FALLBACK_RULES: list[tuple[str, str]] = [
    ("Login Issue",
     r"\b(?:login|log\s?in|sign\s?in|password|otp|can'?t access|cannot access|"
     r"reset)\b|(?:تسجيل\s*الدخول|كلمة\s*(?:المرور|السر)|الدخول)"),
    ("Certificate Issuance",
     r"\b(?:certificate|certif|شهادة)\b|شهادة"),
    ("Exam Result",
     r"\b(?:result|score|grade|pass|fail|نتيجة|درجة|الاختبار)\b"),
    ("Registration Process",
     r"\b(?:register|registration|enrol|enroll|sign up|تسجيل|التسجيل|الاشتراك)\b"),
    ("Fees",
     r"\b(?:fee|fees|price|cost|payment|pay|invoice|refund|رسوم|الرسوم|الدفع|سداد|فاتورة|استرداد)\b"),
    ("Date & Time",
     r"\b(?:date|time|schedule|reschedule|appointment|slot|موعد|التاريخ|الوقت|جدول)\b"),
    ("Course Materials",
     r"\b(?:material|materials|content|video|lesson|link|مواد|المحتوى|الدورة)\b"),
    ("Eligibility / Requirements",
     r"\b(?:eligib|requirement|qualif|الأهلية|متطلبات|الشروط)\b"),
]
_FALLBACK_COMPILED = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in _FALLBACK_RULES]


def _clean_label(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "<na>"}:
        return None
    return s


def _fallback(text: str) -> str:
    for name, regex in _FALLBACK_COMPILED:
        if regex.search(text):
            return name
    return UNCLASSIFIED


def classify(df: pd.DataFrame) -> pd.DataFrame:
    """Add `is_noise`, `themes_list`, and `theme_primary` columns to df."""
    out = df.copy()
    subj = out["subject"].astype("string").fillna("")
    desc = out["description_text"].astype("string").fillna("")

    service = out[SERVICE_COL].astype("string") if SERVICE_COL in out.columns else pd.Series([pd.NA] * len(out))
    inquiry = out[INQUIRY_COL].astype("string") if INQUIRY_COL in out.columns else pd.Series([pd.NA] * len(out))
    type_col = out["type"].astype("string") if "type" in out.columns else pd.Series([pd.NA] * len(out))

    is_noise = (
        (service == NOISE_SERVICE)
        | (inquiry == NOISE_INQUIRY)
        | (type_col == NOISE_TYPE)
        | subj.str.contains(_NOISE_RE, na=False)
    )
    out["is_noise"] = is_noise.fillna(False).to_numpy()

    combined = (subj + " " + desc)
    themes: list[str] = []
    for i in range(len(out)):
        label = _clean_label(inquiry.iloc[i])
        if label and label != NOISE_INQUIRY:
            themes.append(label)
        else:
            themes.append(_fallback(combined.iloc[i].lower()))

    out["theme_primary"] = themes
    out["themes_list"] = [[t] if t else [] for t in themes]
    out.loc[out["is_noise"], "theme_primary"] = "Noise (test / automation)"
    out.loc[out["is_noise"], "themes_list"] = pd.Series(
        [[] for _ in range(int(out["is_noise"].sum()))],
        index=out.index[out["is_noise"].to_numpy()],
    )
    return out
