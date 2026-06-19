"""Generate the static support-insights HTML report for ThinkProp.

Built on the AccessRP report engine, refined for ThinkProp's training/education
domain — 5-card hero KPI strip, 9 tabs, Chart.js charts, narrative pain-category
cards, ticket browser. Issues are driven by ThinkProp's agent-labelled inquiry
taxonomy (lib/themes.py); PII is redacted in embedded quotes.

Usage:
    python3 build_report.py                       # latest month (auto)
    python3 build_report.py --month 2026-05       # specific month
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from lib.text_clean import clean_description, extract_sentences  # noqa: E402
from lib.themes import classify  # noqa: E402

PARQUET = Path(__file__).parent / "data" / "tickets.parquet"
OUT_HTML = Path(__file__).parent / "docs" / "index.html"

# Public deploy location — used for absolute Open Graph / Twitter card URLs.
SITE_URL = "https://ymedhatadres.github.io/thinkprop-support-insights/"
OG_IMAGE = SITE_URL + "og-image.png"   # ThinkProp icon (501x501), served from docs/


# ----------------------------------------------------------------------------
# PII redaction
# ----------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}")
_EID_RE = re.compile(r"\b\d{3}[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d\b")
_LONGREF_RE = re.compile(r"\b\d{12,}\b")
_UNIT_RE = re.compile(r"\bUNT\d+\b", re.IGNORECASE)
_LICENSE_RE = re.compile(r"\bCN-\d+\b", re.IGNORECASE)
_PASSPORT_RE = re.compile(r"\b[A-Z]\d{7,9}\b")
_URL_RE = re.compile(r"https?://\S+")


def redact(text: str) -> str:
    if not text:
        return ""
    t = text
    t = _URL_RE.sub("[url]", t)
    t = _EMAIL_RE.sub("[email]", t)
    t = _EID_RE.sub("[id]", t)
    t = _LONGREF_RE.sub("[ref]", t)
    t = _UNIT_RE.sub("[unit]", t)
    t = _LICENSE_RE.sub("[license]", t)
    t = _PASSPORT_RE.sub("[passport]", t)
    t = _PHONE_RE.sub("[phone]", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ----------------------------------------------------------------------------
# Inquiry type -> pain category mapping (6 ThinkProp domain categories).
# Inquiry types come from the agent-labelled cf_inquiry_type328109 field.
# ----------------------------------------------------------------------------

THEME_TO_CATEGORY = {
    # 1. Enrollment & Registration
    "Registration Process":             "enrollment",
    "Eligibility / Requirements":       "enrollment",
    "Account / Record Update Request":  "enrollment",
    "Attempts":                         "enrollment",
    "Become Instractor":                "enrollment",
    "Partnership":                      "enrollment",
    "Course Recommendation":            "enrollment",

    # 2. Access, Content & Guidance
    "Login Issue":                      "access_content",
    "Course Materials":                 "access_content",
    "Course Link":                      "access_content",
    "TAMM/DARI Guidance":               "access_content",
    "Language":                         "access_content",
    "Dashboard & LMS Navigation":       "access_content",

    # 3. Exams & Results
    "Exam Result":                      "exams",
    "Score Objection":                  "exams",
    "Exam Technical Issue":             "exams",

    # 4. Scheduling & Logistics
    "Date & Time":                      "scheduling",
    "Course Reschedule":                "scheduling",
    "Exam Reschedule":                  "scheduling",
    "Duration":                         "scheduling",
    "Location":                         "scheduling",

    # 5. Certificates & Invoicing
    "Certificate Issuance":             "certificates",
    "Invoice":                          "certificates",
    "Certificate / Designation":        "certificates",
    "Contract / Corporate Invoice":     "certificates",
    "Download Issue":                   "certificates",
    "Format / Structure":               "certificates",

    # 6. Payments & Refunds
    "Fees":                             "payments",
    "Refund Request":                   "payments",
    "Discount Request":                 "payments",
    "Payment Issue":                    "payments",

    # Catch-all for tickets agents left unlabelled.
    "Other / unclassified":             "other",
}

# Default category key for any theme not in THEME_TO_CATEGORY.
OTHER_CATEGORY = "other"

# Narrative content for each of the 6 ThinkProp pain categories.
PAIN_CATEGORIES = {
    "enrollment": {
        "title": "Enrollment & Registration",
        "subtitle": "Getting signed up for the right course or exam is the single biggest source of contact.",
        "why": "The journey from interest to a confirmed seat is not self-evident. "
               "Learners write in to register for a course or exam, to ask whether "
               "they are eligible, to confirm prerequisites and number of attempts, "
               "or to fix a wrong detail on their record. The volume here says the "
               "path from 'I want this course' to 'I am registered' still depends on "
               "a human on the support side.",
        "fix": "Make registration fully self-serve with an eligibility check shown "
               "up front (before payment), a clear prerequisites / attempts summary "
               "on every course card, and a self-service profile editor so learners "
               "can correct their own records without opening a ticket.",
        "color": "#f25f5c",
        "bg": "#fdecec",
    },
    "access_content": {
        "title": "Access, Content & Guidance",
        "subtitle": "Learners can't get in, can't find the material, or need help navigating the wider process.",
        "why": "Once enrolled, friction shifts to access: login problems, missing or "
               "broken course links, materials they can't locate, dashboard / LMS "
               "navigation, and language. A distinct slice is TAMM/DARI guidance — "
               "learners treating ThinkProp support as the help desk for the wider "
               "government licensing journey, not just the course.",
        "fix": "Harden the login / password-reset flow and surface course links and "
               "materials in one obvious place inside the LMS. Add a short in-product "
               "'how the licensing journey works' explainer (incl. TAMM/DARI steps) "
               "so learners self-serve the process questions that currently land in "
               "support.",
        "color": "#3a6ea5",
        "bg": "#e8f0f8",
    },
    "exams": {
        "title": "Exams & Results",
        "subtitle": "Questions and disputes about exam outcomes, scores, and exam-day technical issues.",
        "why": "Exams carry the highest emotional stakes in the journey. Learners "
               "chase results, contest scores, and report technical problems during "
               "the exam itself. Because a licence can hinge on the outcome, these "
               "tickets are time-sensitive and sentiment-heavy.",
        "fix": "Publish results on a predictable, communicated timeline with an "
               "in-product results page. Give a transparent, self-serve score-review "
               "/ objection process with status tracking, and add a pre-exam "
               "technical check to cut exam-day failures.",
        "color": "#5b5fc7",
        "bg": "#ecedf9",
    },
    "scheduling": {
        "title": "Scheduling & Logistics",
        "subtitle": "Dates, times, rescheduling, duration and location of courses and exams.",
        "why": "A large share of contact is pure logistics: what date/time is my "
               "session, can I reschedule a course or exam, how long does it run, "
               "and where is it held. These are simple questions, but today they "
               "require a reply because the answer isn't reliably visible to the "
               "learner after they book.",
        "fix": "Show each learner their schedule (date, time, duration, location / "
               "join link) in one place, and allow self-serve rescheduling within "
               "policy. Send automated reminders with the same details so learners "
               "stop asking support to confirm them.",
        "color": "#0c838f",
        "bg": "#e1f0f1",
    },
    "certificates": {
        "title": "Certificates & Invoicing",
        "subtitle": "Issuing certificates and the paperwork — invoices, designations, formats — that follows completion.",
        "why": "After completion, learners need proof: certificate issuance, the "
               "right name / designation on it, the correct format, and invoices for "
               "themselves or their employer. When any of these is delayed or wrong, "
               "it blocks the learner from using what they paid for (e.g. licensing).",
        "fix": "Auto-issue certificates on completion to a self-serve download in the "
               "learner's dashboard, with names / designations pulled from a verified "
               "profile. Generate invoices (incl. corporate) automatically and make "
               "them downloadable without contacting finance.",
        "color": "#e0892e",
        "bg": "#fbf0e2",
    },
    "payments": {
        "title": "Payments & Refunds",
        "subtitle": "Fees, payment failures, refunds and discount requests.",
        "why": "Money questions are smaller in volume but high in friction: what does "
               "a course cost, a payment that didn't go through, a refund request, or "
               "a discount / promo query. Each unresolved one risks an abandoned "
               "enrollment.",
        "fix": "Show transparent, up-to-date pricing on every course; make checkout "
               "failures recoverable with a clear error and retry; and offer a "
               "self-serve refund / receipt flow within policy so routine money "
               "questions don't need an agent.",
        "color": "#2f9e6f",
        "bg": "#e6f5ee",
    },
    "other": {
        "title": "Other / unclassified",
        "subtitle": "Tickets agents did not tag with an inquiry type.",
        "why": "A residual slice of tickets carries no inquiry-type label, so they "
               "can't be attributed to a specific pain. Most are miscellaneous "
               "one-offs; a consistent tagging discipline would shrink this further.",
        "fix": "Make inquiry-type selection a required field at ticket close so every "
               "contact is attributable in future reporting.",
        "color": "#9aa0a6",
        "bg": "#eceef0",
    },
}

# Categories that represent real, narratable pain (excludes the catch-all).
PAIN_KEYS = [k for k in PAIN_CATEGORIES if k != "other"]

# Keywords used to pull the most salient sentence per inquiry type.
THEME_KEYWORDS = {
    "Registration Process": ["register", "registration", "enrol", "enroll", "sign up",
                              "تسجيل", "التسجيل", "الاشتراك"],
    "Eligibility / Requirements": ["eligible", "eligibility", "requirement", "qualify",
                                    "prerequisite", "الأهلية", "الشروط", "متطلبات"],
    "Account / Record Update Request": ["update", "correct", "wrong", "change my",
                                         "تحديث", "تعديل", "تصحيح"],
    "Login Issue": ["login", "log in", "sign in", "password", "otp", "access",
                    "تسجيل الدخول", "كلمة المرور", "الدخول"],
    "Course Materials": ["material", "materials", "content", "video", "lesson",
                         "المحتوى", "المواد", "الدورة"],
    "Course Link": ["link", "url", "join", "zoom", "الرابط"],
    "TAMM/DARI Guidance": ["tamm", "dari", "تام", "داري"],
    "Language": ["language", "arabic", "english", "اللغة", "بالعربية"],
    "Dashboard & LMS Navigation": ["dashboard", "lms", "navigate", "portal", "لوحة"],
    "Exam Result": ["result", "score", "grade", "pass", "fail",
                    "نتيجة", "النتيجة", "درجة"],
    "Score Objection": ["objection", "appeal", "re-mark", "recheck", "dispute",
                        "اعتراض", "تظلم"],
    "Exam Technical Issue": ["exam error", "technical", "crashed", "froze",
                             "مشكلة تقنية", "خطأ"],
    "Date & Time": ["date", "time", "when", "schedule", "موعد", "التاريخ", "الوقت"],
    "Course Reschedule": ["reschedule", "postpone", "change date", "إعادة جدولة", "تأجيل"],
    "Exam Reschedule": ["reschedule exam", "postpone exam", "تأجيل الاختبار"],
    "Duration": ["duration", "how long", "hours", "مدة", "كم ساعة"],
    "Location": ["location", "where", "address", "venue", "المكان", "العنوان"],
    "Certificate Issuance": ["certificate", "certif", "شهادة"],
    "Invoice": ["invoice", "receipt", "فاتورة", "إيصال"],
    "Fees": ["fee", "fees", "price", "cost", "how much", "رسوم", "الرسوم", "السعر"],
    "Refund Request": ["refund", "money back", "استرداد", "استرجاع"],
    "Discount Request": ["discount", "promo", "offer", "coupon", "خصم", "عرض"],
    "Payment Issue": ["payment failed", "didn't go through", "deducted", "paid",
                      "الدفع", "سداد", "خصم"],
}


# ----------------------------------------------------------------------------
# Data prep
# ----------------------------------------------------------------------------


def load_full() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    df = classify(df)
    return df.loc[~df["is_noise"]].reset_index(drop=True)


def load_period(month: str | None, quarter: str | None,
                all_data: bool) -> tuple[pd.DataFrame, str]:
    df = load_full()
    if all_data:
        return df, f"{df['created_at'].min():%b %Y} – {df['created_at'].max():%b %Y}"
    if month:
        sub = df[df["created_month"] == month].reset_index(drop=True)
        if sub.empty:
            avail = sorted(df["created_month"].dropna().unique().tolist())
            raise SystemExit(
                f"No tickets in month '{month}'. Available: {avail}")
        label = f"{pd.to_datetime(month).strftime('%B %Y')}"
        return sub, label
    if quarter:
        sub = df[df["quarter"] == quarter].reset_index(drop=True)
        return sub, quarter
    # Default: most recent month that has more than a trivial amount of data.
    counts = df["created_month"].value_counts().sort_index()
    real_months = counts[counts >= 50].index.tolist()
    latest = real_months[-1] if real_months else counts.index[-1]
    sub = df[df["created_month"] == latest].reset_index(drop=True)
    label = pd.to_datetime(latest).strftime("%B %Y")
    return sub, label


def build_all_months(min_size: int = 50) -> tuple[dict, list[tuple[str, str]], str]:
    """Build one DATA block per month + an 'all' block.

    Returns (all_data_dict, options_list, default_month_key).
    options_list is [(key, label), ...] ordered with the most recent month first
    and 'all' last — suitable for the <select> dropdown directly.
    """
    df = load_full()
    counts = df["created_month"].value_counts().sort_index()
    months = [m for m in counts.index if counts[m] >= min_size]

    all_data: dict[str, dict] = {}
    options: list[tuple[str, str]] = []

    for m in reversed(months):  # latest first
        sub = df[df["created_month"] == m].reset_index(drop=True)
        label = pd.to_datetime(m).strftime("%B %Y")
        all_data[m] = build_data(sub, label)
        options.append((m, label))

    # "All" view — only worth showing once there is more than one month.
    if len(months) > 1:
        all_label = (f"All months ({df['created_at'].min():%b %Y} – "
                     f"{df['created_at'].max():%b %Y})")
        all_data["all"] = build_data(df, all_label)
        options.append(("all", all_label))

    default_key = options[0][0]  # latest month
    return all_data, options, default_key


def ticket_category(row: pd.Series) -> str | None:
    """Map a ticket to its primary pain category via its first theme match.

    Returns None for un-themed tickets so they don't inflate any of the
    five pain categories.
    """
    for t in (row["themes_list"] or []):
        if t in THEME_TO_CATEGORY:
            return THEME_TO_CATEGORY[t]
    return None


def pick_quote(sub: pd.DataFrame, theme: str | None) -> tuple[str | None, int | None]:
    """Return (cleaned redacted quote, ticket id) for the most negative ticket
    in the slice that has a usable description. None if nothing fits."""
    kw = THEME_KEYWORDS.get(theme or "", []) if theme else []
    cand = (
        sub[sub["description_text"].notna()]
        .sort_values("sentiment_score", ascending=True, na_position="last")
        .head(40)
    )
    for _, r in cand.iterrows():
        c = clean_description(r["description_text"])
        if not c or len(c) < 30:
            continue
        s = extract_sentences(c, kw) if kw else c
        s = redact(s)[:260]
        if len(s) < 25:
            continue
        return s, int(r["id"])
    return None, None


def build_data(df: pd.DataFrame, period_label: str) -> dict:
    df = df.copy()
    df["category"] = df.apply(ticket_category, axis=1)

    total = len(df)
    n_users = int(df["requester.email"].nunique())

    # ---- pain category counts (un-themed tickets excluded) ----
    themed_df = df[df["category"].notna()]
    cat_counts_raw = themed_df["category"].value_counts().to_dict()
    cat_counts = {k: int(cat_counts_raw.get(k, 0)) for k in PAIN_CATEGORIES}
    n_themed = int(themed_df.shape[0])
    n_unthemed = total - n_themed

    # Largest *real* pain category (ignore the unclassified catch-all).
    top_cat_key = max(PAIN_KEYS, key=lambda k: cat_counts.get(k, 0))
    top_cat_share = cat_counts[top_cat_key] / total * 100 if total else 0
    # Repurposed KPI input: share of tickets left unclassified by agents.
    op_noise_share = round(cat_counts.get("other", 0) / total * 100, 1) if total else 0

    # ---- top 10 user-facing issues = top 10 themes by ticket count ----
    theme_to_tickets: dict[str, list[int]] = {}
    for _, r in df.iterrows():
        for t in (r["themes_list"] or []):
            theme_to_tickets.setdefault(t, []).append(int(r["id"]))
    theme_counts = sorted(
        ((t, len(ids)) for t, ids in theme_to_tickets.items()),
        key=lambda x: x[1], reverse=True
    )
    top_user_issues = []
    for theme, count in theme_counts[:10]:
        cat_key = THEME_TO_CATEGORY.get(theme, OTHER_CATEGORY)
        ids = theme_to_tickets[theme]
        sub = df[df["themes_list"].apply(lambda lst, t=theme: t in (lst or []))]
        q_text, q_id = pick_quote(sub, theme)
        sample_ids = sub.sort_values("sentiment_score").head(6)["id"].astype(int).tolist()
        top_user_issues.append({
            "name": theme,
            "description": _issue_description(theme),
            "count": count,
            "pct": round(count / total * 100, 1) if total else 0,
            "category": cat_key,
            "samples": sample_ids[:4],
            "sample_quote": q_text,
            "sample_quote_id": q_id,
        })

    # ---- all issues for distribution table ----
    all_issues = [
        {
            "name": theme,
            "count": count,
            "pct": round(count / total * 100, 1) if total else 0,
            "category": PAIN_CATEGORIES[THEME_TO_CATEGORY.get(theme, OTHER_CATEGORY)]["title"],
            "category_key": THEME_TO_CATEGORY.get(theme, OTHER_CATEGORY),
        }
        for theme, count in theme_counts
    ]

    # ---- top services ----
    svc_counts = df["custom_fields.cf_service"].dropna().value_counts()
    top_services = [(s, int(c)) for s, c in svc_counts.head(5).items()]
    all_services = [(s, int(c)) for s, c in svc_counts.head(10).items()]

    # ---- top issues within each top service ----
    top_service_issues = {}
    for svc, _ in top_services:
        ssub = df[df["custom_fields.cf_service"] == svc]
        local_counts: Counter = Counter()
        for lst in ssub["themes_list"]:
            for t in (lst or []):
                local_counts[t] += 1
        issues = []
        for t, c in local_counts.most_common(4):
            cat_key = THEME_TO_CATEGORY.get(t, OTHER_CATEGORY)
            q, qid = pick_quote(ssub[ssub["themes_list"].apply(
                lambda lst, x=t: x in (lst or []))], t)
            issues.append({
                "name": t,
                "count": int(c),
                "category": cat_key,
                "sample_quote": q,
                "sample_quote_id": qid,
            })
        top_service_issues[svc] = {
            "total": len(ssub),
            "avg_sentiment": round(float(ssub["sentiment_score"].mean()), 1)
              if ssub["sentiment_score"].notna().any() else None,
            "users": int(ssub["requester.email"].nunique()),
            "top_issues": issues,
            "samples": ssub.sort_values("sentiment_score").head(4)["id"].astype(int).tolist(),
        }

    # ---- service × category table (top 10 services) ----
    cat_keys = list(PAIN_CATEGORIES.keys())
    service_category_table = []
    for svc, total_svc in all_services:
        ssub = df[df["custom_fields.cf_service"] == svc]
        bd = ssub["category"].value_counts().to_dict()
        for k in cat_keys:
            bd.setdefault(k, 0)
        service_category_table.append({
            "service": svc,
            "total": int(total_svc),
            "breakdown": {k: int(bd.get(k, 0)) for k in cat_keys},
        })

    # ---- recurring pain points (one per real category, with stats) ----
    pain_points = []
    for k in sorted(PAIN_KEYS, key=lambda x: cat_counts.get(x, 0), reverse=True):
        cfg = PAIN_CATEGORIES[k]
        cat_df = df[df["category"] == k]
        q, qid = pick_quote(cat_df, None)
        sample_ids = cat_df.sort_values("sentiment_score").head(4)["id"].astype(int).tolist()
        pain_points.append({
            "key": k,
            "title": cfg["title"],
            "subtitle": cfg["subtitle"],
            "why": cfg["why"],
            "fix": cfg["fix"],
            "color": cfg["color"],
            "bg": cfg["bg"],
            "count": int(len(cat_df)),
            "pct": round(len(cat_df) / total * 100, 1) if total else 0,
            "sample_quote": q,
            "sample_quote_id": qid,
            "samples": sample_ids,
        })

    # ---- operational observations (channel, tagging, product mix) ----
    operational_issues = []
    chat_df = df[df.get("source_label").astype("string") == "Chat"] \
        if "source_label" in df.columns else df.iloc[0:0]
    if not chat_df.empty:
        operational_issues.append({
            "name": "Live chat is the dominant contact channel",
            "description": f"{len(chat_df):,} of {total:,} tickets "
                           f"({len(chat_df)/total*100:.0f}%) arrive through the live-chat "
                           "widget rather than email or the portal. Chat threads are short "
                           "and conversational — good for deflection with an assistant or "
                           "a strong help centre, but they also mean answers aren't "
                           "captured anywhere reusable.",
            "count": int(len(chat_df)),
            "samples": chat_df.head(4)["id"].astype(int).tolist(),
        })
    eng_df = df[df.get("custom_fields.cf_products").astype("string").str.contains(
        "ENG", na=False)] if "custom_fields.cf_products" in df.columns else df.iloc[0:0]
    if not eng_df.empty:
        operational_issues.append({
            "name": "TP Engineering shares one support desk with the core platform",
            "description": f"{len(eng_df):,} tickets relate to the engineering line "
                           "(exams and courses for civil / structural / architecture). "
                           "It rides the same inbox and the same friction (registration, "
                           "scheduling, results) as the real-estate catalogue — worth a "
                           "dedicated view as that line grows.",
            "count": int(len(eng_df)),
            "samples": eng_df.head(4)["id"].astype(int).tolist(),
        })
    other_df = df[df["category"] == "other"]
    if not other_df.empty:
        operational_issues.append({
            "name": "Unclassified tickets weaken reporting",
            "description": f"{len(other_df):,} tickets "
                           f"({len(other_df)/total*100:.0f}%) were closed without an "
                           "inquiry-type label, so they can't be attributed to a pain "
                           "category. Making the field required at close would tighten "
                           "every metric in this report.",
            "count": int(len(other_df)),
            "samples": other_df.head(4)["id"].astype(int).tolist(),
        })

    # ---- recommendations (priority-tagged, tied to pain categories) ----
    access_impact = sum(theme_to_tickets.get(t, []).__len__()
                        for t in ("Login Issue", "Course Materials", "Course Link"))
    recommendations = [
        ("High", "Make enrollment & eligibility fully self-serve", "enrollment",
         "Enrollment & Registration is the largest pain category — a self-serve "
         "flow with an up-front eligibility check removes the biggest source of "
         "contact.",
         cat_counts.get("enrollment", 0)),
        ("High", "Publish exam results on a fixed, communicated timeline", "exams",
         "Most exam tickets are learners chasing results. A predictable results "
         "page with a known release time deflects them entirely.",
         theme_to_tickets.get("Exam Result", []).__len__()),
        ("High", "Auto-issue certificates & invoices to a self-serve download",
         "certificates",
         "Certificate and invoice requests are mechanical and follow completion — "
         "ideal for full automation into the learner dashboard.",
         cat_counts.get("certificates", 0)),
        ("High", "Show each learner their schedule + allow self-serve reschedule",
         "scheduling",
         "Date/time, reschedule, duration and location questions are pure logistics "
         "answerable by surfacing the booking and reminders.",
         cat_counts.get("scheduling", 0)),
        ("Medium", "Harden login and surface course links/materials in the LMS",
         "access_content",
         "Login, missing links and lost materials are recurring access blockers "
         "with straightforward product fixes.",
         access_impact),
        ("Medium", "Transparent pricing, recoverable checkout & self-serve refunds",
         "payments",
         "Each unresolved money question risks an abandoned enrollment; most can be "
         "answered without an agent.",
         cat_counts.get("payments", 0)),
        ("Medium", "Add a self-serve score-review / objection workflow", "exams",
         "Score objections are sensitive and benefit from a transparent, "
         "status-tracked process instead of email back-and-forth.",
         theme_to_tickets.get("Score Objection", []).__len__()),
        ("Low", "Add an in-product TAMM/DARI licensing-journey explainer",
         "access_content",
         "Learners use support as the help desk for the wider government licensing "
         "process; a short explainer deflects these process questions.",
         theme_to_tickets.get("TAMM/DARI Guidance", []).__len__()),
    ]
    recommendations = [{
        "priority": p, "title": t, "category_key": k,
        "impact_tickets": int(impact),
        "impact_text": f"~{impact:,} addressable tickets" if impact else "",
        "rationale": r,
    } for (p, t, k, r, impact) in recommendations]

    # ---- trends ----
    arabic_chars = df["description_text"].fillna("").str.contains(r"[؀-ۿ]")
    pct_arabic = arabic_chars.mean() * 100 if total else 0
    sub_eng = df[~arabic_chars]
    sub_ar = df[arabic_chars]
    eng_sent = sub_eng["sentiment_score"].mean()
    ar_sent = sub_ar["sentiment_score"].mean()
    top_service_name, top_service_n = (top_services[0] if top_services else ("—", 0))
    top_service_pct = top_service_n / total * 100 if total else 0
    top_cat_cfg = PAIN_CATEGORIES[top_cat_key]
    chat_pct = (len(chat_df) / total * 100) if total else 0
    exams_df = df[df["category"] == "exams"]
    exams_sent = exams_df["sentiment_score"].mean() if not exams_df.empty else float("nan")
    trends = [
        {"title": f"Enrollment is the centre of gravity — {top_cat_cfg['title']} is the largest pain category",
         "detail": f"{cat_counts.get(top_cat_key, 0):,} tickets "
                   f"({top_cat_share:.0f}%) sit in {top_cat_cfg['title']}. {top_cat_cfg['subtitle']} "
                   "Getting learners from interest to a confirmed seat without a human is "
                   "the highest-leverage area."},
        {"title": f"One course — {top_service_name} — concentrates {top_service_pct:.0f}% of contact",
         "detail": f"{top_service_n:,} of {total:,} tickets relate to {top_service_name}. "
                   "Fixing the journey for this single course delivers the largest "
                   "immediate volume win."},
        {"title": f"{pct_arabic:.0f}% of tickets are written in Arabic — they raise the same issues",
         "detail": f"Arabic-language tickets bucket into the same inquiry types as "
                   f"English ones (registration, results, certificates, scheduling). "
                   f"Average sentiment is comparable (EN: {eng_sent:.0f}, AR: {ar_sent:.0f}). "
                   "Every self-serve fix must ship bilingually."},
        {"title": f"Live chat carries {chat_pct:.0f}% of contact",
         "detail": "Most learners reach support through the chat widget, not email. "
                   "That favours deflection through a well-stocked help centre and an "
                   "in-product assistant over email macros."},
        {"title": "Exam-related tickets are the most time-sensitive",
         "detail": f"Results, score objections and exam-day technical issues "
                   f"({cat_counts.get('exams', 0):,} tickets, avg sentiment "
                   f"{exams_sent:.0f}) are tied to licensing outcomes, so they carry the "
                   "highest urgency per ticket even when volume is moderate."},
        {"title": f"{n_users:,} unique learners account for {total:,} tickets",
         "detail": f"Average {total/n_users:.1f} tickets per learner. Most contact is "
                   "one-and-done, so deflection — not faster replies — is the real lever."},
    ]

    # ---- ticket browser ----
    def _txt(val, default=""):
        if val is None or (isinstance(val, float) and pd.isna(val)) or val is pd.NA:
            return default
        s = str(val)
        return default if s in ("", "nan", "<NA>", "None") else s

    tickets = []
    for _, r in df.iterrows():
        primary_theme = (r["themes_list"][0] if r["themes_list"] else None)
        cat_key = ticket_category(r)
        tickets.append({
            "id": int(r["id"]),
            "s": _txt(r.get("custom_fields.cf_service"), "—"),
            "sub": redact(_txt(r.get("subject")))[:150],
            "q": redact(clean_description(r.get("description_text")))[:240],
            "issue": primary_theme or "Other / unclassified",
            "category": PAIN_CATEGORIES[cat_key]["title"] if cat_key in PAIN_CATEGORIES else "Other",
            "category_key": cat_key,
        })

    # ---- final ----
    return {
        "total": total,
        "n_users": n_users,
        "n_themed": n_themed,
        "n_unthemed": n_unthemed,
        "period": period_label,
        "pain_categories": PAIN_CATEGORIES,
        "cat_counts": cat_counts,
        "top_cat_key": top_cat_key,
        "top_cat_share": round(top_cat_share, 1),
        "op_noise_share": round(op_noise_share, 1),
        "top_user_issues": top_user_issues,
        "all_issues": all_issues,
        "top_services": top_services,
        "all_services": all_services,
        "top_service_issues": top_service_issues,
        "service_category_table": service_category_table,
        "pain_points": pain_points,
        "operational_issues": operational_issues,
        "recommendations": recommendations,
        "trends": trends,
        "tickets": tickets,
    }


def _issue_description(theme: str) -> str:
    """Short one-liner explaining each inquiry type in plain language."""
    return {
        "Registration Process": "Learners asking to be registered for a course or "
            "exam, or needing help completing sign-up.",
        "Exam Result": "Learners chasing exam outcomes — when results are out, what "
            "they scored, and whether they passed.",
        "Certificate Issuance": "Requests for the completion certificate: status, "
            "delays, and getting a copy to download.",
        "Date & Time": "Questions about the date and time of a scheduled course or "
            "exam session.",
        "Login Issue": "Sign-in, password and account-access problems on the "
            "platform.",
        "Eligibility / Requirements": "Whether a learner qualifies for a course / "
            "exam and what the prerequisites are.",
        "Score Objection": "Learners contesting an exam score and asking for a "
            "re-mark or review.",
        "Fees": "Questions about course or exam pricing and what a fee covers.",
        "TAMM/DARI Guidance": "Help with the wider government licensing journey "
            "(TAMM / DARI) that lands in ThinkProp support.",
        "Course Reschedule": "Requests to move a booked course to a different date.",
        "Course Materials": "Trouble finding or accessing course content, videos and "
            "lesson materials.",
        "Exam Reschedule": "Requests to move a booked exam to a different date.",
        "Course Link": "Missing or broken links to join or access a course.",
        "Account / Record Update Request": "Learners asking to correct a wrong detail "
            "on their account or record.",
        "Language": "Language of the course / exam / interface, or requests for "
            "Arabic vs English.",
        "Invoice": "Requests for an invoice or receipt for a purchase.",
        "Refund Request": "Requests to refund a paid course or exam.",
        "Duration": "How long a course or exam runs.",
        "Exam Technical Issue": "Technical failures experienced during the exam "
            "itself.",
        "Dashboard & LMS Navigation": "Difficulty navigating the dashboard / learning "
            "management system.",
        "Discount Request": "Requests for a discount, promo code or special offer.",
        "Payment Issue": "Payments that failed, didn't go through, or were charged "
            "incorrectly.",
        "Attempts": "How many attempts a learner has for an exam, and re-sit rules.",
        "Certificate / Designation": "The name or professional designation shown on a "
            "certificate.",
        "Location": "Where a course or exam is physically held.",
        "Become Instractor": "People asking how to become a ThinkProp instructor.",
        "Partnership": "Partnership and business-collaboration enquiries.",
        "Other / unclassified": "Tickets agents did not tag with an inquiry type.",
    }.get(theme, "Recurring inquiry across the period.")


# ----------------------------------------------------------------------------
# HTML rendering
# ----------------------------------------------------------------------------

CSS = """
:root {
  --bg: #f5f7fa;
  --surface: #ffffff;
  --ink: #1a1a1a;
  --muted: #5d6470;
  --border: #e2e6ec;
  /* ThinkProp brand: coral #f25f5c + teal #0c838f */
  --primary: #0e3f47;          /* deep teal — headings & structure */
  --primary-light: #e4f0f1;    /* light teal tint */
  --gold: #f25f5c;             /* coral accent (active tab, quote rule) */
  --teal: #0c838f;
  --coral: #f25f5c;
  --green: #2E7D32; --green-bg: #e8f5e9;
  --red: #C62828; --red-bg: #ffebee;
  --orange: #F57C00; --orange-bg: #fff3e0;
  --blue: #1565C0; --blue-bg: #e3f2fd;
  --purple: #6A1B9A; --purple-bg: #f3e5f5;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
               'Helvetica Neue', Arial, sans-serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.6;
  font-size: 14px;
}
.container { max-width: 1320px; margin: 0 auto; padding: 24px; }

/* ---- Hero ---- */
header.hero {
  background: linear-gradient(135deg, #0c838f 0%, #0e3f47 100%);
  color: white;
  padding: 56px 24px 64px;
}
header.hero .container { padding: 0 24px; }
.brand-badge {
  display: inline-flex; align-items: center;
  background: #ffffff; border-radius: 10px;
  padding: 10px 16px; margin-bottom: 22px;
  box-shadow: 0 2px 10px rgba(0,0,0,0.12);
}
.brand-badge svg { height: 30px; width: auto; display: block; }
header.hero .label {
  font-size: 12px; opacity: 0.7; text-transform: uppercase;
  letter-spacing: 1.2px; margin-bottom: 8px; font-weight: 500;
}
header.hero h1 {
  font-size: 38px; font-weight: 700; margin: 0 0 12px;
  letter-spacing: -0.5px;
  font-family: Georgia, 'Times New Roman', serif;
}
header.hero .subtitle {
  font-size: 18px; opacity: 0.92; font-weight: 400;
  margin-bottom: 24px; max-width: 800px; line-height: 1.5;
}
header.hero .meta { font-size: 13px; opacity: 0.75; }

.kpi-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px; margin-top: 32px;
}
.kpi-card {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 10px;
  padding: 18px;
  backdrop-filter: blur(10px);
}
.kpi-card .label {
  font-size: 11px; opacity: 0.85; text-transform: uppercase;
  letter-spacing: 0.6px; margin-bottom: 8px; font-weight: 600;
}
.kpi-card .value { font-size: 32px; font-weight: 700; line-height: 1.1; }
.kpi-card .delta { font-size: 12px; opacity: 0.8; margin-top: 6px; }

/* ---- Month picker (in hero) ---- */
.month-picker {
  display: inline-flex; align-items: center; gap: 10px;
  margin-top: 18px;
  background: rgba(255,255,255,0.10);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 8px; padding: 8px 14px;
}
.month-picker label {
  font-size: 12px; opacity: 0.85; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.5px;
}
.month-picker select {
  background: white; color: var(--primary);
  border: 1px solid rgba(255,255,255,0.4); border-radius: 6px;
  padding: 6px 28px 6px 12px; font-size: 14px; font-weight: 600;
  cursor: pointer; font-family: inherit;
  appearance: none; -webkit-appearance: none;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'><path fill='%230e3f47' d='M2 4 L6 8 L10 4 Z'/></svg>");
  background-repeat: no-repeat; background-position: right 8px center;
}
.month-picker select:focus { outline: 2px solid var(--gold); outline-offset: 2px; }

/* ---- Tab bar ---- */
nav.tabs {
  background: white; border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 50;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
nav.tabs .container {
  display: flex; gap: 4px; overflow-x: auto; padding: 0 24px;
}
nav.tabs button {
  background: none; border: none; padding: 14px 16px;
  font-size: 13px; font-weight: 500; color: var(--muted);
  cursor: pointer; border-bottom: 3px solid transparent;
  white-space: nowrap; transition: color .15s, border-color .15s;
  font-family: inherit;
}
nav.tabs button:hover { color: var(--primary); }
nav.tabs button.active {
  color: var(--primary); border-bottom-color: var(--gold);
  font-weight: 700;
}

/* ---- Sections (tab panels) ---- */
section.tab-content { display: none; padding: 36px 0; }
section.tab-content.active { display: block; }
section h2 {
  font-size: 26px; margin: 0 0 8px; color: var(--primary);
  font-weight: 700;
  font-family: Georgia, 'Times New Roman', serif;
}
section .section-sub {
  color: var(--muted); margin-bottom: 28px;
  font-size: 15px; line-height: 1.6; max-width: 880px;
}

.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
@media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }

.card {
  background: white; border: 1px solid var(--border); border-radius: 10px;
  padding: 22px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.card h3 { margin: 0 0 14px; font-size: 17px; font-weight: 700; color: var(--primary); }
.card h4 { margin: 12px 0 4px; font-size: 14px; }
.chart-container { position: relative; height: 380px; }
.chart-container.tall { height: 520px; }

/* ---- Tables ---- */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td {
  text-align: left; padding: 12px 14px;
  border-bottom: 1px solid var(--border); vertical-align: top;
}
th {
  background: var(--primary-light); color: var(--primary);
  font-weight: 700; font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.4px;
}
tbody tr:nth-child(even) { background: #fafbfc; }
tbody tr:hover { background: #f0f4f8; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }

/* ---- Pills ---- */
.pill {
  display: inline-block; padding: 3px 10px; border-radius: 20px;
  font-size: 11px; font-weight: 700; line-height: 1.6; white-space: nowrap;
  text-transform: uppercase; letter-spacing: 0.04em;
}
/*__CAT_PILLS__*/
.pill.priority-high      { background: var(--red-bg);    color: var(--red); }
.pill.priority-medium    { background: var(--orange-bg); color: var(--orange); }
.pill.priority-low       { background: var(--green-bg);  color: var(--green); }

/* ---- Quotes & ticket IDs ---- */
.ticket-id {
  font-family: 'SF Mono', Monaco, monospace;
  color: var(--primary); font-weight: 700; font-size: 11px;
}
.quote {
  font-style: italic; padding: 10px 14px;
  border-left: 3px solid var(--gold);
  background: var(--primary-light);
  margin: 8px 0; border-radius: 0 6px 6px 0;
  font-size: 13px; color: #333;
}

/* ---- Issue cards (top issues) ---- */
.issue-card {
  display: grid; grid-template-columns: 56px 1fr; gap: 16px;
  background: white; border: 1px solid var(--border); border-radius: 10px;
  padding: 20px; margin-bottom: 14px;
}
.rank-circle {
  width: 56px; height: 56px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px; font-weight: 800;
}
.issue-meta {
  display: flex; gap: 12px; align-items: center;
  font-size: 13px; color: var(--muted); margin-bottom: 8px;
  flex-wrap: wrap;
}
.issue-meta strong { color: var(--ink); }
.issue-name { font-size: 17px; font-weight: 700; margin: 0 0 6px; color: var(--primary); }
.issue-desc { color: var(--ink); font-size: 14px; margin-bottom: 8px; }
.issue-evidence {
  color: var(--muted); font-size: 12px; margin-top: 8px;
  font-family: 'SF Mono', Monaco, monospace;
}

/* ---- Pain point cards (5 mirror cats) ---- */
.pain-card {
  padding: 24px; border-radius: 12px; border: 1px solid;
  margin-bottom: 20px;
}
.pain-card .pain-head {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 14px; gap: 16px; flex-wrap: wrap;
}
.pain-card .pain-title { font-size: 22px; font-weight: 700; margin: 0; }
.pain-card .pain-stat {
  font-family: 'SF Mono', Monaco, monospace;
  font-size: 14px; font-weight: 700;
}
.pain-card .pain-sub { font-size: 14px; opacity: 0.85; margin: 0 0 14px; }
.pain-card h4 {
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.6px;
  margin: 14px 0 4px; color: rgba(0,0,0,0.6);
}
.pain-card p { margin: 4px 0 10px; }
.pain-card .pain-evidence { font-size: 12px; opacity: 0.7; }

/* ---- Recommendations ---- */
.rec {
  background: white; border: 1px solid var(--border); border-radius: 10px;
  padding: 18px 22px; margin-bottom: 12px;
  border-left: 4px solid var(--gold);
}
.rec .rec-head { display: flex; gap: 10px; align-items: center; margin-bottom: 6px; flex-wrap: wrap; }
.rec .rec-title { font-weight: 700; font-size: 15px; color: var(--primary); }
.rec .rec-impact { color: var(--muted); font-size: 12px; }
.rec p { margin: 6px 0 0; font-size: 13px; }

/* ---- Trends ---- */
.trend {
  background: white; border: 1px solid var(--border); border-radius: 8px;
  padding: 16px 20px; margin-bottom: 10px;
}
.trend h4 { margin: 0 0 4px; font-size: 14px; color: var(--primary); }
.trend p { margin: 0; font-size: 13px; color: var(--ink); }

/* ---- Ticket browser ---- */
.filter-bar {
  display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; align-items: center;
}
.filter-bar input, .filter-bar select {
  padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px;
  font-size: 13px; font-family: inherit; min-width: 200px;
}
.filter-bar label { font-size: 12px; color: var(--muted); }
.browser-table { max-height: 70vh; overflow-y: auto; border-radius: 8px;
  border: 1px solid var(--border); }
.browser-table table { font-size: 12.5px; }
.browser-table th { position: sticky; top: 0; z-index: 2; }
"""


# ---- HTML body shell ----

def render_html(all_data: dict, options: list[tuple[str, str]],
                default_key: str) -> str:
    json_all = json.dumps(all_data, ensure_ascii=False, default=str)
    options_html = "\n".join(
        f'<option value="{html.escape(k)}">{html.escape(label)}</option>'
        for k, label in options
    )
    # Inject pill colours generated from the live category palette.
    cat_pills = "\n".join(
        f".pill.cat-{k} {{ background: {c['bg']}; color: {c['color']}; }}"
        for k, c in PAIN_CATEGORIES.items()
    )
    css = CSS.replace("/*__CAT_PILLS__*/", cat_pills)
    # Inline the brand wordmark (kept colour on a white badge so it reads on teal).
    logo_path = Path(__file__).parent / "assets" / "thinkprop_logo.svg"
    logo_svg = logo_path.read_text(encoding="utf-8") if logo_path.exists() else ""
    title = "ThinkProp — Support Insights"

    # Social-share (Open Graph / Twitter) metadata. Image reuses ThinkProp's own
    # brand icon (downloaded to docs/og-image.png), matching the main site.
    d = all_data.get(default_key) or next(iter(all_data.values()))
    og_desc = html.escape(
        f"What learners contact ThinkProp about, which courses carry the most "
        f"load, and the highest-impact product fixes. {d['period']}: "
        f"{d['total']:,} tickets · {d['n_users']:,} learners."
    )
    og = f"""
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="ADRES UX Research">
  <meta property="og:title" content="ThinkProp Support Insights">
  <meta property="og:description" content="{og_desc}">
  <meta property="og:url" content="{SITE_URL}">
  <meta property="og:image" content="{OG_IMAGE}">
  <meta property="og:image:width" content="501">
  <meta property="og:image:height" content="501">
  <meta property="og:image:alt" content="ThinkProp">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="ThinkProp Support Insights">
  <meta name="twitter:description" content="{og_desc}">
  <meta name="twitter:image" content="{OG_IMAGE}">"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{og_desc}">{og}
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>{css}</style>
</head>
<body>

<header class="hero">
  <div class="container">
    <div class="brand-badge">{logo_svg}</div>
    <div class="label">Support Insights</div>
    <h1 id="reportTitle">ThinkProp Support Insights</h1>
    <div class="subtitle">A stakeholder-friendly view of what learners contact
    ThinkProp about, which courses generate the most load, and where the
    highest-impact product fixes lie.</div>
    <div class="meta">Prepared for: Leadership &amp; the ThinkProp Revamp
    &nbsp;•&nbsp; ADRES UX Research &nbsp;•&nbsp;
    {pd.Timestamp.now():%-d %B %Y}</div>

    <div class="month-picker">
      <label for="monthPicker">Reporting period</label>
      <select id="monthPicker">{options_html}</select>
    </div>

    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="label">Tickets Reviewed</div>
        <div class="value" id="kpi-total">—</div>
        <div class="delta" id="kpi-total-delta">—</div>
      </div>
      <div class="kpi-card">
        <div class="label">Distinct Issue Types</div>
        <div class="value" id="kpi-types">—</div>
        <div class="delta">Recurring inquiry types identified</div>
      </div>
      <div class="kpi-card">
        <div class="label">Top Pain Point</div>
        <div class="value" id="kpi-top-cat-pct">—</div>
        <div class="delta" id="kpi-top-cat-name">—</div>
      </div>
      <div class="kpi-card">
        <div class="label">Unique Learners</div>
        <div class="value" id="kpi-users">—</div>
        <div class="delta">Distinct requesters this period</div>
      </div>
      <div class="kpi-card">
        <div class="label">Most Affected Course</div>
        <div class="value" id="kpi-svc-name">—</div>
        <div class="delta" id="kpi-svc-delta">—</div>
      </div>
    </div>
  </div>
</header>

<nav class="tabs">
  <div class="container">
    <button class="tab-btn active" data-tab="exec">Executive Summary</button>
    <button class="tab-btn" data-tab="topissues">Top Issues</button>
    <button class="tab-btn" data-tab="services">Most Affected Courses</button>
    <button class="tab-btn" data-tab="distribution">Issue Distribution</button>
    <button class="tab-btn" data-tab="painpoints">Recurring Pain Points</button>
    <button class="tab-btn" data-tab="operational">Operational Issues</button>
    <button class="tab-btn" data-tab="trends">Key Trends</button>
    <button class="tab-btn" data-tab="recommendations">Recommendations</button>
    <button class="tab-btn" data-tab="browser">Ticket Browser</button>
  </div>
</nav>

<div class="container">

  <section id="exec" class="tab-content active">
    <h2>1. Executive Summary</h2>
    <p class="section-sub" id="execSub">A snapshot of the ThinkProp support
    picture: what learners are contacting us about, which courses are taking the
    heaviest load, and what the highest-impact product moves look like.</p>
    <div class="grid-2">
      <div class="card">
        <h3>How learners are getting stuck</h3>
        <div class="chart-container"><canvas id="painChart"></canvas></div>
      </div>
      <div class="card">
        <h3>Top 5 courses by ticket volume</h3>
        <div class="chart-container"><canvas id="topSvcChart"></canvas></div>
      </div>
    </div>
    <div class="card" style="margin-top:18px;">
      <h3>What leadership should know</h3>
      <ol style="margin: 8px 0; padding-left: 22px; line-height: 1.9;" id="execBullets"></ol>
    </div>
  </section>

  <section id="topissues" class="tab-content">
    <h2>2. Top Issues Affecting Learners</h2>
    <p class="section-sub">The 10 most frequent inquiry types, ranked by volume.
    Each card explains what is happening, the pain category it belongs to, and
    provides example ticket IDs and a redacted learner quote for verification.</p>
    <div id="topIssuesContainer"></div>
  </section>

  <section id="services" class="tab-content">
    <h2>3. Most Affected Courses</h2>
    <p class="section-sub">The five ThinkProp courses receiving the heaviest
    support load, with the specific issues driving each.</p>
    <div id="topServicesContainer"></div>
  </section>

  <section id="distribution" class="tab-content">
    <h2>4. Issue Distribution Across Courses</h2>
    <p class="section-sub">How learner issues spread across different ThinkProp
    courses, with the share each pain category takes inside each course.</p>
    <div class="grid-2">
      <div class="card">
        <h3>Tickets per course</h3>
        <div class="chart-container tall"><canvas id="svcDistChart"></canvas></div>
      </div>
      <div class="card">
        <h3>Pain category mix per course</h3>
        <div class="chart-container tall"><canvas id="svcCatChart"></canvas></div>
      </div>
    </div>
    <div class="card" style="margin-top:18px;">
      <h3>Full breakdown</h3>
      <div id="svcCatTable"></div>
    </div>
  </section>

  <section id="painpoints" class="tab-content">
    <h2>5. Recurring Learner Pain Points</h2>
    <p class="section-sub">The recurring patterns that explain why learners
    contact support. Each card includes the underlying behaviour, why it is
    happening, what would fix it, and example ticket IDs.</p>
    <div id="painCardsContainer"></div>
  </section>

  <section id="operational" class="tab-content">
    <h2>6. Operational Observations</h2>
    <p class="section-sub">Patterns about how support runs — channel mix, product
    coverage, and tagging quality — that shape how to act on everything above,
    rather than learner pain points in themselves.</p>
    <div id="operationalContainer"></div>
  </section>

  <section id="trends" class="tab-content">
    <h2>7. Key Trends &amp; Observations</h2>
    <p class="section-sub">High-level patterns worth surfacing to leadership
    in addition to the headline metrics.</p>
    <div id="trendsContainer"></div>
  </section>

  <section id="recommendations" class="tab-content">
    <h2>8. Recommendations &amp; Opportunities</h2>
    <p class="section-sub">Prioritised actions linked to the underlying pain
    points, with addressable-ticket counts as a rough sizing.</p>
    <div id="recommendationsContainer"></div>
  </section>

  <section id="browser" class="tab-content">
    <h2>9. Ticket Browser</h2>
    <p class="section-sub" id="browserSub">Search and filter the tickets for
    the selected reporting period. Useful for verifying any finding or pulling
    representative examples for a stakeholder discussion. Quotes are
    redacted.</p>
    <div class="filter-bar">
      <div>
        <label>Search</label><br>
        <input type="text" id="browserSearch" placeholder="search subject, body, issue...">
      </div>
      <div>
        <label>Pain Category</label><br>
        <select id="browserCat">
          <option value="">All categories</option>
        </select>
      </div>
      <div>
        <label>Course</label><br>
        <select id="browserSvc">
          <option value="">All courses</option>
        </select>
      </div>
      <div>
        <span id="browserCount" style="font-weight:600;color: var(--primary);"></span>
      </div>
    </div>
    <div class="browser-table">
      <table>
        <thead><tr>
          <th style="width: 70px;">ID</th>
          <th style="width: 160px;">Course</th>
          <th style="width: 150px;">Issue</th>
          <th style="width: 150px;">Pain Category</th>
          <th>Subject / Quote (redacted)</th>
        </tr></thead>
        <tbody id="browserBody"></tbody>
      </table>
    </div>
  </section>

  <p style="color: var(--muted); font-size: 12px; margin-top: 40px;
            padding-top: 18px; border-top: 1px solid var(--border);">
    <strong>Methodology.</strong> Source data: ThinkProp Freshdesk export for the
    period covered. Tickets tagged &ldquo;Ads Test Tickets and Auto-Emails&rdquo;
    and tickets matching auto-reply / delivery-failure patterns are excluded.
    Issues are taken from the agent-labelled inquiry-type field
    (cf_inquiry_type328109); the small share of unlabelled tickets is bucketed by
    a bilingual (English + Arabic) keyword fallback or left as
    &ldquo;Other / unclassified&rdquo;. Quotes are PII-redacted (emails, phones,
    IDs, reference numbers, URLs).
    Generated {pd.Timestamp.now():%Y-%m-%d} · ADRES UX Research.
  </p>
</div>

<script>
const ALL_DATA = {json_all};
const DEFAULT_MONTH = "{html.escape(default_key)}";
{RENDERER_JS}
</script>
</body>
</html>"""


RENDERER_JS = r"""
// ---- Tab nav (static, doesn't depend on month) ----
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const id = btn.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-content').forEach(s => s.classList.toggle('active', s.id === id));
    setHash('#' + id);
    window.scrollTo({ top: 0, behavior: 'smooth' });
    // Charts born in a hidden tab render at 0x0 and resize() can't recover them,
    // so build a tab's charts lazily the first time it becomes visible.
    if (typeof buildLazyCharts === 'function') buildLazyCharts(id);
    requestAnimationFrame(() => {
      Object.values(charts).forEach(c => { try { c.resize(); } catch (e) {} });
    });
  });
});

let DATA = null;
const charts = {};
let buildLazyCharts = null;   // assigned per-render; builds hidden-tab charts on demand

function destroyCharts() {
  for (const k of Object.keys(charts)) {
    try { charts[k] && charts[k].destroy(); } catch(e) {}
    delete charts[k];
  }
}
function clearAllDynamic() {
  ['execBullets','topIssuesContainer','topServicesContainer','svcCatTable',
   'painCardsContainer','operationalContainer','trendsContainer',
   'recommendationsContainer','browserBody'].forEach(id => {
     const el = document.getElementById(id);
     if (el) el.innerHTML = '';
  });
  // Reset browser dropdowns to their default-only options
  const catSel = document.getElementById('browserCat');
  const svcSel = document.getElementById('browserSvc');
  if (catSel) catSel.innerHTML = '<option value="">All categories</option>';
  if (svcSel) svcSel.innerHTML = '<option value="">All courses</option>';
}

function setHash(h) {
  const u = new URL(location.href);
  u.hash = h.startsWith('#') ? h.substring(1) : h;
  history.replaceState(null, '', u.toString());
}
function setMonthParam(m) {
  const u = new URL(location.href);
  u.searchParams.set('m', m);
  history.replaceState(null, '', u.toString());
}

function renderHero() {
  const pc = DATA.pain_categories;
  const topCat = pc[DATA.top_cat_key];
  const topSvcName = (DATA.top_services[0] || ['—', 0])[0];
  const topSvcN = (DATA.top_services[0] || ['—', 0])[1];
  const topSvcPct = DATA.total ? (topSvcN / DATA.total * 100) : 0;

  document.getElementById('reportTitle').textContent =
    'ThinkProp Support Insights — ' + DATA.period;
  document.title = 'ThinkProp — Support Insights (' + DATA.period + ')';

  document.getElementById('kpi-total').textContent = DATA.total.toLocaleString();
  document.getElementById('kpi-total-delta').textContent =
    DATA.period + ' support volume';
  document.getElementById('kpi-types').textContent = DATA.all_issues.length;
  document.getElementById('kpi-top-cat-pct').textContent =
    DATA.top_cat_share.toFixed(1) + '%';
  document.getElementById('kpi-top-cat-name').textContent = topCat.title;
  document.getElementById('kpi-users').textContent =
    (DATA.n_users || 0).toLocaleString();
  document.getElementById('kpi-svc-name').textContent = topSvcName;
  document.getElementById('kpi-svc-delta').textContent =
    topSvcN.toLocaleString() + ' tickets (' + topSvcPct.toFixed(0) + '% of total)';

  document.getElementById('execSub').textContent =
    'A snapshot of the ThinkProp support picture for ' + DATA.period +
    ': what learners are contacting us about, which courses are taking the heaviest load, and what the highest-impact product moves look like.';
  document.getElementById('browserSub').textContent =
    'Search and filter the ' + DATA.total.toLocaleString() +
    ' tickets in ' + DATA.period + '. Useful for verifying any finding or pulling representative examples. Quotes are redacted.';
}

function renderAll(monthKey) {
  DATA = ALL_DATA[monthKey];
  destroyCharts();
  clearAllDynamic();
  renderHero();

  const catKeys = Object.keys(DATA.pain_categories);
  const catColor = k => DATA.pain_categories[k].color;
  const catTitle = k => DATA.pain_categories[k].title;

// ---- Exec: doughnut ----
charts.pain = new Chart(document.getElementById('painChart'), {
  type: 'doughnut',
  data: {
    labels: catKeys.map(catTitle),
    datasets: [{
      data: catKeys.map(k => DATA.cat_counts[k] || 0),
      backgroundColor: catKeys.map(catColor),
      borderWidth: 2, borderColor: '#fff',
    }],
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { position: 'right', labels: { font: { size: 12 }, boxWidth: 14, padding: 10 } },
      tooltip: { callbacks: { label: ctx => {
        const pct = (ctx.parsed / DATA.total * 100).toFixed(1);
        return `${ctx.label}: ${ctx.parsed} tickets (${pct}%)`;
      } } }
    }
  }
});

// ---- Exec: top 5 courses bar ----
charts.topSvc = new Chart(document.getElementById('topSvcChart'), {
  type: 'bar',
  data: {
    labels: DATA.top_services.map(s => s[0]),
    datasets: [{ label: 'Tickets', data: DATA.top_services.map(s => s[1]), backgroundColor: '#0c838f' }],
  },
  options: {
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: { x: { beginAtZero: true } },
  }
});

// ---- Exec bullets ----
{
  const ol = document.getElementById('execBullets');
  // Rank only real pain categories (exclude the unclassified catch-all).
  const realCats = catKeys.filter(k => k !== 'other')
    .sort((a,b) => (DATA.cat_counts[b]||0) - (DATA.cat_counts[a]||0));
  const top1 = realCats[0], top2 = realCats[1], top3 = realCats[2];
  const top1Cfg = DATA.pain_categories[top1];
  const pct = k => ((DATA.cat_counts[k]||0)/DATA.total*100).toFixed(0);
  const topIssue = DATA.top_user_issues[0];
  const topSvc = DATA.top_services[0];
  const bullets = [
    `<strong>${top1Cfg.title} is the largest pain category.</strong> ${DATA.cat_counts[top1]} tickets (${pct(top1)}% of total). ${top1Cfg.subtitle}`,
    `<strong>The single most-reported issue is "${topIssue.name}"</strong> with ${topIssue.count} tickets (${topIssue.pct.toFixed(1)}%). ${topIssue.description}`,
    `<strong>${topSvc[0]} carries ${topSvc[1]} tickets</strong> — ${(topSvc[1]/DATA.total*100).toFixed(0)}% of all contact relates to this one course. Improving its journey has the highest immediate volume impact.`,
    `<strong>${DATA.pain_categories[top2].title} and ${DATA.pain_categories[top3].title} are the next priorities.</strong> ${DATA.cat_counts[top2]} and ${DATA.cat_counts[top3]} tickets respectively. ${DATA.pain_categories[top2].subtitle}`,
    `<strong>Most contact is deflectable.</strong> The leading categories — enrollment, scheduling, results and certificates — are routine, self-serve-able actions; the lever is product self-service, not faster replies.`,
  ];
  bullets.forEach(b => { const li = document.createElement('li'); li.innerHTML = b; ol.appendChild(li); });
}

// ---- Top issues cards ----
{
  const el = document.getElementById('topIssuesContainer');
  DATA.top_user_issues.forEach((issue, i) => {
    const cat = DATA.pain_categories[issue.category];
    const div = document.createElement('div');
    div.className = 'issue-card';
    div.innerHTML = `
      <div class="rank-circle" style="background: ${cat.color}22; color: ${cat.color};">${i+1}</div>
      <div class="issue-body">
        <div class="issue-meta">
          <span class="pill cat-${issue.category}">${cat.title}</span>
          <strong>${issue.count} tickets</strong>
          <span style="color: var(--muted);">(${issue.pct.toFixed(1)}% of all)</span>
        </div>
        <h4 class="issue-name">${issue.name}</h4>
        <div class="issue-desc">${issue.description}</div>
        ${issue.sample_quote ? `<div class="quote">"${issue.sample_quote}" <br/><span class="ticket-id">— Ticket ${issue.sample_quote_id}</span></div>` : ''}
        <div class="issue-evidence">Sample tickets: ${issue.samples.map(id => '#'+id).join(', ')}</div>
      </div>
    `;
    el.appendChild(div);
  });
}

// ---- Top services cards ----
{
  const el = document.getElementById('topServicesContainer');
  Object.entries(DATA.top_service_issues).forEach(([svc, info]) => {
    const div = document.createElement('div');
    div.className = 'card';
    div.style.marginBottom = '14px';
    const rows = info.top_issues.map(iss => {
      const cat = DATA.pain_categories[iss.category];
      return `
        <div style="padding: 10px 0; border-bottom: 1px dashed var(--border);">
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:4px;flex-wrap:wrap;">
            <span class="pill cat-${iss.category}">${cat.title}</span>
            <strong>${iss.name}</strong>
            <span style="color: var(--muted);">${iss.count} tickets</span>
          </div>
          ${iss.sample_quote ? `<div class="quote">"${iss.sample_quote}" <br/><span class="ticket-id">— Ticket ${iss.sample_quote_id}</span></div>` : ''}
        </div>
      `;
    }).join('');
    div.innerHTML = `
      <h3>${svc} <span style="color: var(--muted); font-weight:400; font-size: 14px;">— ${info.total} tickets · ${info.users} users · avg sentiment ${info.avg_sentiment ?? '—'}</span></h3>
      ${rows}
      <div class="issue-evidence" style="margin-top: 10px;">Sample tickets: ${info.samples.map(id => '#'+id).join(', ')}</div>
    `;
    el.appendChild(div);
  });
}

// ---- Distribution charts (built lazily once their tab is visible) ----
// A chart created in a display:none container renders at 0x0 and resize()
// cannot recover it, so defer creation until the tab is first shown.
buildLazyCharts = (tabId) => {
  if (tabId !== 'distribution' || charts.svcDist) return;
  charts.svcDist = new Chart(document.getElementById('svcDistChart'), {
    type: 'bar',
    data: {
      labels: DATA.all_services.map(s => s[0]),
      datasets: [{ data: DATA.all_services.map(s => s[1]), backgroundColor: '#0c838f' }],
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true } },
    }
  });
  charts.svcCat = new Chart(document.getElementById('svcCatChart'), {
    type: 'bar',
    data: {
      labels: DATA.service_category_table.map(r => r.service),
      datasets: catKeys.map(k => ({
        label: DATA.pain_categories[k].title,
        data: DATA.service_category_table.map(r => r.breakdown[k] || 0),
        backgroundColor: DATA.pain_categories[k].color,
      })),
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      scales: { x: { stacked: true, beginAtZero: true }, y: { stacked: true } },
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
    }
  });
};

// ---- Distribution table ----
{
  const el = document.getElementById('svcCatTable');
  let html = '<table><thead><tr><th>Service</th><th class="num">Total</th>';
  catKeys.forEach(k => html += `<th class="num">${DATA.pain_categories[k].title}</th>`);
  html += '</tr></thead><tbody>';
  DATA.service_category_table.forEach(r => {
    html += `<tr><td><strong>${r.service}</strong></td><td class="num">${r.total}</td>`;
    catKeys.forEach(k => html += `<td class="num">${r.breakdown[k] || 0}</td>`);
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

// ---- Pain point cards ----
{
  const el = document.getElementById('painCardsContainer');
  DATA.pain_points.forEach(p => {
    const div = document.createElement('div');
    div.className = 'pain-card';
    div.style.background = p.bg;
    div.style.borderColor = p.color + '55';
    div.style.color = '#222';
    div.innerHTML = `
      <div class="pain-head">
        <h3 class="pain-title" style="color: ${p.color};">${p.title}</h3>
        <div class="pain-stat" style="color: ${p.color};">${p.count} tickets · ${p.pct.toFixed(1)}%</div>
      </div>
      <p class="pain-sub">${p.subtitle}</p>
      <h4>What is happening</h4><p>${p.why}</p>
      <h4>What would fix it</h4><p>${p.fix}</p>
      ${p.sample_quote ? `<div class="quote">"${p.sample_quote}" <br/><span class="ticket-id">— Ticket ${p.sample_quote_id}</span></div>` : ''}
      <div class="pain-evidence">Sample tickets: ${p.samples.map(id => '#'+id).join(', ')}</div>
    `;
    el.appendChild(div);
  });
}

// ---- Operational issues ----
{
  const el = document.getElementById('operationalContainer');
  DATA.operational_issues.forEach(o => {
    const div = document.createElement('div');
    div.className = 'card';
    div.style.marginBottom = '12px';
    div.innerHTML = `
      <h3>${o.name} <span style="color: var(--muted); font-weight:400; font-size: 13px;">— ${o.count} tickets</span></h3>
      <p>${o.description}</p>
      <div class="issue-evidence">Sample tickets: ${o.samples.map(id => '#'+id).join(', ')}</div>
    `;
    el.appendChild(div);
  });
}

// ---- Trends ----
{
  const el = document.getElementById('trendsContainer');
  DATA.trends.forEach(t => {
    const div = document.createElement('div');
    div.className = 'trend';
    div.innerHTML = `<h4>${t.title}</h4><p>${t.detail}</p>`;
    el.appendChild(div);
  });
}

// ---- Recommendations ----
{
  const el = document.getElementById('recommendationsContainer');
  DATA.recommendations.forEach(r => {
    const cat = DATA.pain_categories[r.category_key];
    const div = document.createElement('div');
    div.className = 'rec';
    div.innerHTML = `
      <div class="rec-head">
        <span class="pill priority-${r.priority.toLowerCase()}">${r.priority} priority</span>
        <span class="pill cat-${r.category_key}">${cat ? cat.title : ''}</span>
        <span class="rec-title">${r.title}</span>
        <span class="rec-impact">${r.impact_text}</span>
      </div>
      <p>${r.rationale}</p>
    `;
    el.appendChild(div);
  });
}

// ---- Ticket browser ----
{
  const cats = [...new Set(DATA.tickets.map(t => t.category))].sort();
  const svcs = [...new Set(DATA.tickets.map(t => t.s))].sort();

  // Clone-replace the inputs to drop listeners from any previous render, then
  // keep references to the LIVE nodes so the filter reads current values.
  const fresh = id => {
    const el = document.getElementById(id);
    const n = el.cloneNode(false);
    el.parentNode.replaceChild(n, el);
    return n;
  };
  const search = fresh('browserSearch');
  const catSel = fresh('browserCat');
  const svcSel = fresh('browserSvc');
  const opt = (sel, val, txt) => {
    const o = document.createElement('option'); o.value = val; o.text = txt; sel.appendChild(o);
  };
  opt(catSel, '', 'All categories'); cats.forEach(c => opt(catSel, c, c));
  opt(svcSel, '', 'All courses');    svcs.forEach(s => opt(svcSel, s, s));

  const body = document.getElementById('browserBody');
  const count = document.getElementById('browserCount');
  const LIMIT = 500;
  function renderBrowser() {
    const q = (search.value || '').toLowerCase();
    const c = catSel.value, s = svcSel.value;
    const rows = DATA.tickets.filter(t => {
      if (c && t.category !== c) return false;
      if (s && t.s !== s) return false;
      if (q) {
        const hay = (t.sub + ' ' + t.q + ' ' + t.issue).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const shown = Math.min(rows.length, LIMIT);
    count.textContent = rows.length > LIMIT
      ? `Showing ${shown.toLocaleString()} of ${rows.length.toLocaleString()} matching tickets`
      : `${rows.length.toLocaleString()} of ${DATA.tickets.length.toLocaleString()} tickets`;
    body.innerHTML = rows.slice(0, LIMIT).map(t => `
      <tr>
        <td class="ticket-id">#${t.id}</td>
        <td>${t.s}</td>
        <td>${t.issue}</td>
        <td><span class="pill cat-${t.category_key}">${t.category}</span></td>
        <td><div style="font-weight:600;">${t.sub}</div><div style="color: var(--muted); font-style:italic; margin-top: 4px;">${t.q}</div></td>
      </tr>
    `).join('');
  }
  search.addEventListener('input', renderBrowser);
  catSel.addEventListener('change', renderBrowser);
  svcSel.addEventListener('change', renderBrowser);
  renderBrowser();
}

// If a tab with lazy charts is already active (deep link / month switch), build now.
{
  const activeTab = document.querySelector('.tab-content.active')?.id;
  if (activeTab) {
    buildLazyCharts(activeTab);
    requestAnimationFrame(() => {
      Object.values(charts).forEach(c => { try { c.resize(); } catch (e) {} });
    });
  }
}
}  // end renderAll

// ---- Initial wiring ----
{
  const picker = document.getElementById('monthPicker');
  const urlM = new URLSearchParams(location.search).get('m');
  const initial = (urlM && ALL_DATA[urlM]) ? urlM : DEFAULT_MONTH;
  picker.value = initial;
  picker.addEventListener('change', e => {
    renderAll(e.target.value);
    setMonthParam(e.target.value);
  });
  renderAll(initial);
  // Restore tab from URL hash, if any.
  if (location.hash) {
    const h = location.hash.replace('#','');
    const btn = document.querySelector(`.tab-btn[data-tab="${h}"]`);
    if (btn) btn.click();
  }
}
"""


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="default month to open the report on, e.g. '2026-05'")
    args = parser.parse_args()

    all_data, options, default_key = build_all_months()
    if args.month:
        if args.month not in all_data:
            avail = [k for k, _ in options]
            raise SystemExit(f"No data for month '{args.month}'. Available: {avail}")
        default_key = args.month

    months_with_data = [k for k, _ in options if k != "all"]
    total_all = all_data.get("all", all_data[default_key])["total"]
    print(f"Embedding {len(months_with_data)} month(s) + 'all' view "
          f"(total {total_all:,} tickets after noise filtering). Default: {default_key}.")

    html_str = render_html(all_data, options, default_key)

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html_str, encoding="utf-8")
    print(f"Wrote {OUT_HTML}  ({OUT_HTML.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
