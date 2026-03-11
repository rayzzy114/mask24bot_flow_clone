# Invalid Input Handling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent the bot from advancing on malformed user input and return clear, state-aware error messages for common invalid entries.

**Architecture:** Keep the existing FSM and transition model intact. Strengthen `FlowRuntime` input validation by classifying expected input from state text, generating a state-aware error response, and leaving the user in the same state on invalid input.

**Tech Stack:** Python, aiogram v3, pytest

---

### Task 1: Add failing regression tests

**Files:**
- Modify: `tests/test_reported_issues_fix.py`
- Test: `tests/test_reported_issues_fix.py`

**Step 1: Write failing tests**

Add tests for:
- invalid verification card number returns a card-specific error and does not advance
- invalid amount text returns an amount-specific error and does not advance
- text sent in verification-photo state returns a photo-specific error
- invalid generic text input falls back to a generic validation error

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_reported_issues_fix.py -k "invalid_card or invalid_amount or verification_photo_requires_photo or generic_invalid_input" -v`
Expected: FAIL because current runtime emits generic or missing errors.

### Task 2: Implement minimal runtime changes

**Files:**
- Modify: `app/runtime.py`
- Test: `tests/test_reported_issues_fix.py`

**Step 1: Add state-aware validation helpers**

Implement helpers that:
- detect expected input kind from the current state's text
- return a specific error message for card, amount, crypto-address, photo, or generic input

**Step 2: Integrate validation into `on_message`**

Ensure invalid input:
- does not advance state
- replies with the specific error message
- keeps existing valid flows unchanged

**Step 3: Keep implementation minimal**

Avoid refactoring unrelated transition logic or changing catalog behavior.

### Task 3: Verify

**Files:**
- Test: `tests/test_reported_issues_fix.py`

**Step 1: Run focused tests**

Run: `pytest tests/test_reported_issues_fix.py -k "invalid_card or invalid_amount or verification_photo_requires_photo or generic_invalid_input" -v`

**Step 2: Run broader regression slice**

Run: `pytest tests/test_reported_issues_fix.py -v`

**Step 3: Review output**

Confirm the new tests pass and existing runtime behavior remains green.
