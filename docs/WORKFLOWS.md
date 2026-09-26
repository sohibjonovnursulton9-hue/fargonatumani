# Workflows Document

> **Note**: All workflows involving legal, regulatory, or privacy steps are marked with `[CONFIRM]` and require official confirmation from legal authorities.

## Citizen Workflows

### 1. First-time User Registration Flow
- **Trigger**: User sends `/start` command.
- **Steps**:
  1. Prompt for language selection (Uzbek/Russian).
  2. Display terms of service and privacy policy.
  3. [CONFIRM] User accepts consent.
  4. Prompt for phone number verification.
  5. Route to main menu.
- **Actors**: Citizen, System.
- **Expected Outcomes**: New `users` and `consent_records` rows created.
- **Error Cases**: User rejects consent, invalid phone number.

### 2. Complaint Submission Flow
- **Trigger**: Citizen selects "Submit Complaint" from main menu.
- **Steps**:
  1. Select category.
  2. Select target organization/MFY.
  3. Provide personal information (if not already saved).
  4. Input complaint details (text).
  5. Upload attachments (optional).
  6. Preview submission.
  7. Confirm and submit.
- **Actors**: Citizen.
- **Expected Outcomes**: `complaints` record created; tracking ID issued.
- **Error Cases**: Invalid attachment format, timeout during submission.

### 3. Check Complaint Status Flow
- **Trigger**: Citizen selects "Check Status" or inputs tracking ID.
- **Steps**: Lookup ID in DB and return current status and recent events.
- **Expected Outcomes**: Status message displayed.

### 4. Receive and Respond to Agency Response
- **Trigger**: Agency submits a response.
- **Steps**: Citizen receives notification with response text and attachments.

### 5. Citizen Confirmation Flow (Resolved Yes/No)
- **Trigger**: After receiving agency response.
- **Steps**: Prompt citizen: "Is the issue resolved?". Citizen taps Yes or No.
- **Expected Outcomes**: Complaint marked as `Resolved` or sent back for `Review`.

### 6. Draft Recovery
- **Trigger**: User returns after timeout or bot restart.
- **Steps**: Bot checks `complaint_drafts` and asks if user wants to resume.

### 7. Language Change Flow
- **Trigger**: User selects "Change Language" from settings.
- **Expected Outcomes**: UI updates immediately.

### 8. Duplicate Complaint Handling
- **Trigger**: Submission matches recent active complaint.
- **Steps**: System identifies similarity and warns citizen.

## Admin Workflows

### 1. Login and Authentication
- **Trigger**: Admin navigates to login page.
- **Steps**: Enter credentials → validate → issue secure session token.

### 2. Dashboard Overview
- **Trigger**: Successful login.
- **Steps**: Load aggregate statistics (new, pending, overdue complaints).

### 3. Triage New Complaint
- **Trigger**: Admin selects a `New` complaint.
- **Steps**: Review content → assign category/urgency → set initial deadline.

### 4. Assign to Agency/Executor
- **Trigger**: Triage step completion.
- **Steps**: Select target agency/staff → create assignment record → notify agency.

### 5. Reassign / Redirect Complaint
- **Trigger**: Agency rejects assignment or admin overrides.
- **Steps**: Admin selects new agency and updates assignment.

### 6. Review Agency Response Before Sending
- **Trigger**: Agency drafts response (if moderation is enabled).
- **Steps**: Admin approves/rejects response.

### 7. Deadline Extension Workflow
- **Trigger**: Agency requests extension.
- **Steps**: Admin reviews justification → [CONFIRM] approves/rejects based on regulatory limits.

### 8. Escalation Workflow
- **Trigger**: Complaint passes deadline or manually escalated.
- **Steps**: Notify higher authority, update priority.

### 9. Statistics and Reporting
- **Trigger**: Admin requests report.
- **Steps**: Generate PDF/Excel of system metrics.

### 10. User/Role Management
- **Trigger**: Super-admin manages staff.
- **Steps**: Create, update, or revoke access for `admin_users`.

### 11. Category/Organization Management
- **Trigger**: Admin updates system taxonomy.
- **Steps**: Add/edit/disable categories or organizations.

## Agency Staff Workflows

### 1. View Assigned Complaints
- **Trigger**: Agency staff logs in.
- **Expected Outcomes**: List of active assignments filtered by their organization.

### 2. Request Additional Info from Citizen
- **Trigger**: Agency needs clarification.
- **Steps**: Submit request via Admin UI → routed to Citizen via Bot.

### 3. Submit Response with Evidence
- **Trigger**: Issue is addressed.
- **Steps**: Write response → upload photos/documents → submit.

### 4. Mark Implementation Complete
- **Trigger**: Physical work completed.
- **Expected Outcomes**: Status changes to `Pending Citizen Confirmation`.

## System Automated Workflows

### 1. Deadline Approaching Notification
- **Trigger**: 3 days before `deadline`.
- **Steps**: Background job finds matches → notifies assigned agency.

### 2. Deadline Exceeded Escalation
- **Trigger**: Current time > `deadline`.
- **Steps**: System marks assignment `Overdue` → notifies Admin.

### 3. Citizen Follow-up Reminder
- **Trigger**: 48 hours without citizen confirmation.
- **Steps**: Bot sends reminder.

### 4. Notification Retry
- **Trigger**: Telegram API error (e.g., rate limit).
- **Steps**: Job retries sending after exponential backoff.

### 5. Rate Limit Enforcement
- **Trigger**: Too many requests from a user.
- **Steps**: System temporarily blocks user and logs event.
