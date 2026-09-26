# FTMT: Requirements Documentation (Talablar Hujjati)

Ushbu hujjat Farg'ona Tuman Murojaatlar Tizimining (FTMT) funksional va nofunksional talablarini belgilaydi.

> [!CAUTION]
> **LEGAL & COMPLIANCE WARNING**
> - **Legal Validation:** All consent texts, data processing agreements, and category mappings MUST be reviewed and approved by a qualified lawyer.
> - **Assumptions:** Current categories, deadlines, and notification logic are based on provisional assumptions and require official confirmation from district administration.
> - **PII:** Data minimization and privacy regulations must be strictly adhered to.

## 1. Functional Requirements

### 1.1 Telegram Bot Interface (Citizen Flow)
- **Language Selection:** Interface language (Uzbek/Russian).
- **Language of Complaint:** Explicit distinction between the interface language and the complaint submission language (Uzbek Latin / Uzbek Cyrillic / Russian).
- **Consent:** Mandatory acceptance of terms/privacy policy before submission. *(Requires Legal Approval)*
- **Submission Flow:**
  - **Citizen Info:** Full name, phone number, address (selectable from MFY list).
  - **Complaint Type:** Ariza (Application), Shikoyat (Complaint), Taklif (Proposal).
  - **Routing/Category:** Selection of relevant category or organization (based on 17 provisional categories).
  - **Content:** Title and detailed description.
  - **Attachments:** Support for files/images.
- **Review & Confirm:** Preview step to edit/confirm before final submission.
- **Tracking:** Unique tracking ID issued upon confirmation.

### 1.2 State Machine (Status FSM)
Murojaat quyidagi holatlardan (statuslardan) birida bo'lishi mumkin:
- `DRAFT` - Qoralama
- `SUBMITTED` - Yuborilgan
- `TRIAGE` - Saralash/Ko'rib chiqish (Admin tomonidan)
- `ROUTED` - Yo'naltirilgan
- `IN_PROGRESS` - Jarayonda
- `WAITING_FOR_CITIZEN` - Fuqarodan javob kutilyapti
- `RESPONSE_PROVIDED` - Javob berilgan
- `IMPLEMENTATION_REPORTED` - Bajarilganligi haqida hisobot
- `CITIZEN_CONFIRMATION_PENDING` - Fuqaro tasdig'i kutilyapti
- `RESOLVED` - Hal qilingan
- `REOPENED` - Qayta ochilgan
- `REJECTED` - Rad etilgan
- `WITHDRAWN` - Qaytarib olingan
- `REFERRED_OUTSIDE` - Boshqa tashkilotga yuborilgan
- `OUT_OF_SCOPE` - Vakolat doirasidan tashqari

### 1.3 Citizen Confirmation Flow
- Agency marks the issue as `RESPONSE_PROVIDED` or `IMPLEMENTATION_REPORTED`.
- Bot asks the citizen: "Is the issue resolved?" (Yes/No).
- **Yes:** Moves to `RESOLVED`.
- **No:** Moves to `REOPENED` or escalates.
- **No response:** Auto-resolves after a configurable timeout *(Assumption: 3 days, needs confirmation)*.

### 1.4 Duplicate Detection
- Rate limits per Telegram ID.
- Text similarity checks to show a "Hint" to the admin.
- **Rule:** Never auto-reject automatically based on similarity.

### 1.5 Admin Panel & RBAC
- **Roles:** `super_admin`, `district_supervisor`, `agency_head`, `executor`, `auditor`.
- **Functions:**
  - Search, filter, and view complaints.
  - Triage (accept, reject, reroute).
  - Assign to executors / reassign.
  - Respond to citizens.
  - Deadline management and extension requests (requires reason and citizen notification).
  - Aggregate statistics.

### 1.6 Deadlines & Timers
- Deadlines must be configurable in the database, NOT hardcoded.
- **Provisional Defaults (Needs Official Confirmation):**
  - Ariza / Shikoyat: 15 days.
  - Taklif: 30 days.
  - Forwarding/Routing: 5 days.

### 1.7 Background Jobs
- Deadline reminders for executors.
- Automated follow-ups for citizen confirmation (`CITIZEN_CONFIRMATION_PENDING`).
- Notification retry logic for failed Telegram messages.

### 1.8 Statistics & Reporting
- Aggregated data (No PII).
- Median and 95th percentile response times.
- Status breakdowns per agency/category.

## 2. Non-Functional Requirements

### 2.1 Security
- **Encryption:** TLS/SSL for API and Database connections. Secure storage for sensitive API keys.
- **RBAC:** Strict access controls in the admin API.
- **Audit Trail:** Log all state changes and admin actions.
- **PII Redaction:** Personal details must be redacted in application logs.
- **Rate Limiting:** Protect bot endpoints and APIs from abuse.

### 2.2 Privacy
- **Data Minimization:** Collect only necessary info.
- **Consent Tracking:** Log the timestamp and version of the legal terms accepted.
- **Retention Policy:** Automated archiving/deletion of PII after the legal retention period *(Assumption: Needs Legal definition)*.

### 2.3 Reliability
- **Transactions & Idempotency:** DB transactions for state changes. Webhook processing must be idempotent.
- **Draft Recovery:** Temporary caching/storage of partial submissions.
- **Error Handling:** Graceful degradation, clear user-facing error messages in the correct language.

### 2.4 Timezone Handling
- All internal timestamps stored in **UTC**.
- Display times localized to **Asia/Tashkent (UTC+5)**.

### 2.5 Scalability
- Modular structure (FastAPI + Telegram Bot).
- Queue-ready architecture (Celery/Redis or similar can be plugged in for heavy background jobs later).

### 2.6 Localization
- **Interface:** 2 languages (Uzbek, Russian).
- **Submissions:** Support 3 input scripts (Uzbek Latin, Uzbek Cyrillic, Russian) for processing.
