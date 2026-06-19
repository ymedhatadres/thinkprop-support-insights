# ThinkProp — Support Insights Report

A self-contained, static HTML report that turns ThinkProp's Freshdesk support
tickets into a stakeholder-friendly view of what learners contact support about,
which courses generate the most load, and where the highest-impact product fixes
lie. Built on the AccessRP report engine, re-tuned for ThinkProp's
training/education domain and brand.

**Deliverable:** [`docs/index.html`](docs/index.html) — one file, no build step to
view. Open it in any browser (Chart.js loads from CDN).

## How it works

1. **`preprocess.py`** reads the Freshdesk Excel export, normalises it
   (status/priority/source labels, timestamps, sentiment buckets recalibrated to
   ThinkProp's distribution, derived date columns) and writes `data/tickets.parquet`.
2. **`lib/themes.py`** classifies each ticket. ThinkProp's export already carries
   an agent-labelled inquiry taxonomy in `cf_inquiry_type328109` (32 issue types),
   so that field *is* the primary issue. A bilingual (EN + AR) keyword fallback
   buckets the small share of unlabelled tickets; test/auto-email tickets are
   filtered as noise.
3. **`build_report.py`** maps the 32 inquiry types into 6 pain categories,
   computes KPIs/charts/quotes (PII-redacted), and renders `docs/index.html`.

## Regenerate

```bash
pip install -r requirements.txt
python3 preprocess.py        # Excel export -> data/tickets.parquet
python3 build_report.py      # parquet -> docs/index.html
```

To preview locally: `python3 -m http.server --directory docs` then open
http://localhost:8000 (or use the `report` config in `.claude/launch.json`).

## Data source

Raw export (not committed):
`~/Downloads/May_FreshDesk_Export/ThinkProp/tickets_full_export_MERGED.xlsx`
— set in `SOURCE_FILES` at the top of `preprocess.py`. The current report covers
**May 2026** (937 tickets after noise filtering, 235 unique learners). The
pipeline already supports multiple months: drop additional monthly exports into
`SOURCE_FILES` and a month picker appears automatically.

## Pain categories

| Category | Inquiry types folded in |
|---|---|
| Enrollment & Registration | Registration, Eligibility, Account/Record Update, Attempts, Instructor, Partnership |
| Access, Content & Guidance | Login, Course Materials, Course Link, TAMM/DARI Guidance, Language, Dashboard/LMS |
| Exams & Results | Exam Result, Score Objection, Exam Technical Issue |
| Scheduling & Logistics | Date & Time, Course/Exam Reschedule, Duration, Location |
| Certificates & Invoicing | Certificate Issuance, Invoice, Designation, Corporate Invoice, Download, Format |
| Payments & Refunds | Fees, Refund, Discount, Payment Issue |

Tickets agents left unlabelled (and not caught by the keyword fallback) appear as
a small neutral "Other / unclassified" slice and are excluded from the narrative
pain cards.

## Sections (9 tabs)

Executive Summary · Top Issues · Most Affected Courses · Issue Distribution ·
Recurring Pain Points · Operational Observations · Key Trends · Recommendations ·
Ticket Browser.
