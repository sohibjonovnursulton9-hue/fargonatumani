# Security and Privacy / Xavfsizlik va Maxfiylik

Ushbu hujjat Farg'ona tumani fuqarolar murojaatlarini boshqarish tizimining xavfsizlik va maxfiylik tamoyillarini tavsiflaydi.

## Threat Model (Xavf Modeli)
* **Unauthorized access to PII:** Fuqarolarning shaxsiy ma'lumotlariga ruxsatsiz kirish.
* **Admin account compromise:** Administrator akkauntlarini buzish.
* **Telegram bot token leak:** Telegram bot tokenining sizib chiqishi.
* **SQL injection, XSS, CSRF:** Tizimga kod orqali hujumlar.
* **File upload attacks:** Zararli dasturlar yoki o'ta katta hajmdagi fayllarni yuklash.
* **Spam/abuse of complaint system:** Tizimga ketma-ket asossiz murojaatlar yuborish.
* **Data exfiltration by insider:** Ichki xodimlar tomonidan ma'lumotlarni o'g'irlash.
* **Man-in-the-middle attacks:** Tarmoq orqali ma'lumotlar almashinuvini ushlab qolish.
* **Denial of service (DoS):** Tizim ishlashiga to'sqinlik qilish hujumlari.

## Authentication & Authorization (Autentifikatsiya va Avtorizatsiya)
* **Admin:** 
  * Session-based auth with bcrypt password hashing.
  * CSRF tokens.
  * Session expiry (configurable, default 8 hours).
  * Login attempt throttling (5 attempts then 15 min lockout).
  * MFA-ready (TOTP placeholder).
* **Citizens (Fuqarolar):** Identified by Telegram user ID, no password required.
* **RBAC (Role-Based Access Control):** 
  * `super_admin`, `district_supervisor`, `agency_head`, `executor`, `auditor`.
* **Permission matrix:** Each role has specific access scopes defined in the access matrix.

## Data Protection (Ma'lumotlarni Himoyalash)
* **Encryption at rest:** Database-level. (SQLite encryption extension for dev, PostgreSQL pgcrypto for prod) - **[NEEDS IMPLEMENTATION for production]**.
* **Encryption in transit:** HTTPS/TLS for all API endpoints, Telegram API uses TLS.
* **PII fields:** full_name, phone, address, complaint text, attachments.
* **Log redaction:** Structured logging with automatic PII field masking.
* **Backup encryption:** Required for any backup containing PII.

## Data Retention & Deletion (Ma'lumotlarni Saqlash va O'chirish)
* **Retention periods:** **[NEEDS LEGAL CONFIRMATION]**. Default assumption: complaints retained for legal minimum period (suggest 5 years, NEEDS CONFIRMATION).
* **Consent records:** Retained indefinitely for audit purposes.
* **Audit trail:** Immutable, never deleted.
* **Right to deletion:** **[NEEDS LEGAL FRAMEWORK CONFIRMATION for Uzbekistan]**.
* **Draft auto-cleanup:** 30 days after the last update.

## File Attachment Security (Fayl Xavfsizligi)
* **Allowed types:** jpg, jpeg, png, pdf, doc, docx, mp4 (configurable).
* **Max file size:** 20MB (Telegram limit).
* **Storage:** Telegram `file_id` reference + optional local download.
* **Malware scanning:** **[PLACEHOLDER]** - needs integration with antivirus service.
* **File access:** Only authorized roles via secure API endpoint.

## Audit Trail (Audit Jurnali)
* All state changes logged immutably.
* **Fields:** `timestamp`, `actor_id`, `actor_type`, `action`, `entity_type`, `entity_id`, `old_value`, `new_value`, `ip_address`, `user_agent`.
* No `UPDATE` or `DELETE` allowed on the audit table.
* Bulk export requires `super_admin` role + audit log entry.

## Rate Limiting (So'rovlar Cheklovi)
* **Telegram bot:** Max 3 complaints per user per 24 hours (configurable).
* **Admin API:** Standard rate limiting per IP and per user.
* **Login attempts:** 5 per 15 minutes per IP.

## Incident Response (Hodisalarga Munosabat)
* **[PLACEHOLDER]** - Needs organizational incident response plan.
* Bot token rotation procedure.
* Admin password reset procedure.
* Data breach notification: **[NEEDS LEGAL FRAMEWORK]**.

## Pre-Launch Security Checklist
* Checklist of items that must be verified and signed off before production deployment.
