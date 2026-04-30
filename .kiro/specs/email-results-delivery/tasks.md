# Implementation Plan: email-results-delivery

## Overview

Extend the scheduler to email scored results as a CSV attachment after each run. Changes touch `setup_prompt.py` (collect recipient email), a new `src/email_sender.py` module, and `scheduler.py` (Step 6b). SMTP credentials come exclusively from environment variables; the recipient address is the only email value persisted in config.

## Tasks

- [x] 1. Add `hypothesis` to dev dependencies
  - In `pyproject.toml`, add `hypothesis` to the `[dependency-groups] dev` list if not already present.
  - _Requirements: testing infrastructure for property-based tests_

- [x] 2. Extend `setup_prompt.py` with email collection
  - [x] 2.1 Implement `_prompt_email()` and wire into `run_setup()`
    - Add `_prompt_email()` that loops until the user enters a string with exactly one `@` and at least one `.` in the domain part; print a validation error and re-prompt on failure.
    - Call `_prompt_email()` inside `run_setup()` after `iteration_count` is collected.
    - Add `recipient_email` key to the returned config dict.
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 2.2 Write unit tests for `_prompt_email()` in `tests/test_setup_prompt.py`
    - Test that `run_setup()` returns a dict containing `recipient_email`.
    - Test that invalid addresses (no `@`, two `@`, no `.` in domain) cause re-prompting.
    - Test that a valid address is accepted on first attempt.
    - _Requirements: 1.2, 1.3_

  - [x] 2.3 Write property test for email validation (Property 1)
    - **Property 1: Email validation accepts valid addresses and rejects invalid ones**
    - Use Hypothesis to generate arbitrary strings; assert the validation logic accepts iff the string has exactly one `@` and the domain part contains at least one `.`.
    - **Validates: Requirements 1.2, 1.3**

- [x] 3. Create `src/email_sender.py`
  - [x] 3.1 Implement `_read_smtp_settings()`
    - Read `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` from `os.environ`.
    - Raise `EnvironmentError` listing all missing required variables (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`) if any are absent.
    - Return `(host, int(port), username, password_or_None)`.
    - _Requirements: 2.1, 2.4, 2.5_

  - [x] 3.2 Write property test for missing SMTP vars (Property 4)
    - **Property 4: Missing required SMTP variables produce a descriptive EnvironmentError**
    - Use Hypothesis to generate non-empty subsets of `{SMTP_HOST, SMTP_PORT, SMTP_USERNAME}`; assert `_read_smtp_settings()` raises `EnvironmentError` naming every missing variable.
    - **Validates: Requirements 2.4**

  - [x] 3.3 Implement `_build_message()`
    - Build a `MIMEMultipart` with `From` = `smtp_username`, `To` = `recipient`, `Subject` = `Job Finder Results – <YYYY-MM-DD>` (UTC date of `run_date`).
    - Attach a plain-text body: `Jobs scored: N\nNew jobs added: N\nTotal jobs in CSV: N`.
    - Attach the DataFrame serialised to CSV as `results_<YYYY-MM-DD>.csv` with MIME type `text/csv`.
    - _Requirements: 3.2, 3.3, 3.4_

  - [x] 3.4 Write property test for message structure (Property 5)
    - **Property 5: Built message structure is correct for any inputs**
    - Use Hypothesis to generate varied DataFrames, `run_date` values, summary dicts, and recipient addresses; assert `To`, `Subject`, body content, and attachment filename all match the spec.
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

  - [x] 3.5 Implement `_connect_and_send()`
    - If `port == 465`, use `smtplib.SMTP_SSL`; otherwise use `smtplib.SMTP` and call `starttls()`.
    - Call `login(username, password)` only when `password` is not `None`.
    - Call `send_message(message)` then `quit()`.
    - _Requirements: 2.2, 2.3, 2.5_

  - [x] 3.6 Write property test for STARTTLS on non-465 ports (Property 3)
    - **Property 3: STARTTLS is used for any port other than 465**
    - Use Hypothesis to generate integer port values != 465; mock `smtplib.SMTP` and `smtplib.SMTP_SSL`; assert `SMTP` is used and `starttls()` is called, and `SMTP_SSL` is never used.
    - **Validates: Requirements 2.3**

  - [x] 3.7 Write unit tests for `_connect_and_send()` and `_read_smtp_settings()`
    - Test `SMTP_SSL` is used when port is 465.
    - Test `login()` is skipped when `SMTP_PASSWORD` is absent.
    - Test `_read_smtp_settings()` returns correct tuple when all env vars are set.
    - _Requirements: 2.1, 2.2, 2.5_

  - [x] 3.8 Implement public `send_results_email()`
    - Call `_read_smtp_settings()`, `_build_message()`, `_connect_and_send()` in sequence.
    - Propagate `EnvironmentError` and `smtplib.SMTPException` to the caller.
    - _Requirements: 3.1_

- [x] 4. Checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Extend `scheduler.py` with Step 6b email delivery
  - [x] 5.1 Import `send_results_email` and add Step 6b block
    - Add `from src.email_sender import send_results_email` at the top of `scheduler.py`.
    - After the existing Step 6 summary print, insert Step 6b: guard on `recipient` and non-empty `result_df`, then call `send_results_email(result_df, run_date, summary, recipient)`.
    - On success print `[{_ts()}] Email delivered to {recipient}.` to stdout.
    - Catch `EnvironmentError` → print warning to stderr identifying missing vars, skip email.
    - Catch all other exceptions → `logging.exception(...)` + print warning to stderr, continue.
    - _Requirements: 3.1, 3.5, 3.6, 3.7, 4.1, 4.2_

  - [x] 5.2 Write unit tests for scheduler email integration in `tests/test_scheduler.py`
    - Test that `send_results_email` is not called when `recipient_email` is absent from config.
    - Test that `send_results_email` is not called when `result_df` is empty.
    - Test that scheduler continues (no `sys.exit`) when `send_results_email` raises `SMTPException`.
    - Test that stdout confirmation line contains the recipient address on success.
    - _Requirements: 3.5, 3.6, 3.7, 4.2_

  - [x] 5.3 Write property test for stdout confirmation (Property 6)
    - **Property 6: Scheduler stdout confirmation contains the recipient address**
    - Use Hypothesis to generate valid recipient address strings; mock `send_results_email` to succeed; assert the captured stdout line contains the recipient address.
    - **Validates: Requirements 3.6**

  - [x] 5.4 Write property test for SMTP exception handling (Property 7)
    - **Property 7: SMTP exception causes warning to stderr without scheduler exit**
    - Use Hypothesis to generate exception types subclassing `Exception`; mock `send_results_email` to raise them; assert no `sys.exit()` is called and stderr contains a warning.
    - **Validates: Requirements 3.7**

  - [x] 5.5 Write property test for recipient_email round-trip (Property 2)
    - **Property 2: recipient_email round-trips through config save/load**
    - Use Hypothesis to generate valid email strings; call `save_config` then `load_config`; assert the loaded `recipient_email` equals the original.
    - **Validates: Requirements 1.4, 4.3**

- [x] 6. Final checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP.
- Each task references specific requirements for traceability.
- Property tests use the **Hypothesis** library (add to `pyproject.toml` dev deps in task 1).
- `merge_results()` is always called before Step 6b; email failure never prevents local CSV update.
- Existing configs without `recipient_email` load and run without error (backward compatible).
