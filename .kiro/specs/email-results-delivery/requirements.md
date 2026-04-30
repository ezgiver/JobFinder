# Requirements Document

## Introduction

This feature extends the Job Finder scheduler so that after each autonomous run the scored results CSV is emailed to the user's configured email address. The user provides their email address once during the existing setup prompt. After every run the Scheduler attaches the current run's results as a CSV file and sends it via SMTP. Local CSV storage via `result_store.merge_results()` is preserved alongside email delivery.

## Glossary

- **Scheduler**: The CLI component in `scheduler.py` responsible for running the pipeline and persisting results.
- **Email_Sender**: The new component responsible for composing and sending the results email via SMTP.
- **Config_Store**: The component that reads and writes the persistent JSON configuration file (`~/.job_finder/config.json`).
- **Setup_Prompt**: The interactive CLI session that collects all required configuration on first launch.
- **Result_Store**: The component that reads and writes the cumulative results CSV file.
- **SMTP_Settings**: The set of values required to connect to an outbound mail server: host, port, username, password, and TLS mode.
- **Results_CSV**: The CSV attachment containing the scored job results from a single scheduler run.
- **Recipient_Address**: The email address supplied by the user during setup, to which results are delivered.

---

## Requirements

### Requirement 1: Email Address Collection During Setup

**User Story:** As a job seeker, I want to provide my email address once during setup, so that the Scheduler can deliver results to me automatically without further interaction.

#### Acceptance Criteria

1. WHEN the Setup_Prompt is collecting configuration, THE Setup_Prompt SHALL prompt the user for a Recipient_Address.
2. WHEN the user provides a Recipient_Address, THE Setup_Prompt SHALL validate that the value contains exactly one `@` character and at least one `.` in the domain part before accepting it.
3. IF the user provides a value that fails email validation, THEN THE Setup_Prompt SHALL display a validation error and re-prompt for a valid Recipient_Address.
4. WHEN all inputs are valid, THE Config_Store SHALL persist the `recipient_email` field alongside the existing configuration fields.

---

### Requirement 2: SMTP Configuration

**User Story:** As a job seeker, I want to configure my outbound mail server settings once, so that the Scheduler can send emails on my behalf without storing credentials insecurely.

#### Acceptance Criteria

1. THE Email_Sender SHALL read SMTP connection settings from the following environment variables: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`.
2. WHERE `SMTP_PORT` is 465, THE Email_Sender SHALL connect using implicit TLS (SMTP_SSL).
3. WHERE `SMTP_PORT` is not 465, THE Email_Sender SHALL connect using STARTTLS after establishing the initial connection.
4. IF any of `SMTP_HOST`, `SMTP_PORT`, or `SMTP_USERNAME` environment variables are not set at run time, THEN THE Scheduler SHALL print a descriptive error message identifying the missing variable(s) and skip email delivery for that run without exiting.
5. IF `SMTP_PASSWORD` is not set, THEN THE Email_Sender SHALL attempt to authenticate without a password.

---

### Requirement 3: Results Email Delivery

**User Story:** As a job seeker, I want to receive the scored results as a CSV attachment after each run, so that I can review new job matches directly in my inbox.

#### Acceptance Criteria

1. WHEN a run produces scored results and SMTP_Settings are present, THE Email_Sender SHALL send an email to the Recipient_Address with the Results_CSV attached.
2. THE Email_Sender SHALL set the email subject to `Job Finder Results – <ISO-8601 date of run>` where the date is the UTC date the run completed.
3. THE Email_Sender SHALL include a plain-text body summarising the run: number of jobs scored, number of new jobs added to the cumulative CSV, and total jobs in the cumulative CSV.
4. THE Email_Sender SHALL attach the Results_CSV as a file named `results_<YYYY-MM-DD>.csv` where the date is the UTC date the run completed.
5. WHEN a run produces zero scored results, THE Scheduler SHALL skip email delivery for that run.
6. WHEN email delivery succeeds, THE Scheduler SHALL print a timestamped confirmation line to stdout including the Recipient_Address.
7. IF the SMTP connection or send operation raises an exception, THEN THE Scheduler SHALL log the error to the log file and print a human-readable warning to stderr, and SHALL continue normal operation without exiting.

---

### Requirement 4: Backward Compatibility

**User Story:** As a job seeker, I want local CSV storage to continue working as before, so that I still have a local copy of all results even when email delivery is configured.

#### Acceptance Criteria

1. WHEN a run produces scored results, THE Result_Store SHALL merge results into the Cumulative_CSV regardless of whether email delivery is configured or succeeds.
2. WHEN the `recipient_email` field is absent from the saved configuration, THE Scheduler SHALL skip email delivery and continue normal operation without error.
3. THE Config_Store SHALL remain backward-compatible: existing configuration files without `recipient_email` SHALL load and operate correctly.
