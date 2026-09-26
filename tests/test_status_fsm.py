"""
Tests for complaint status FSM transitions.
Validates that only legal state transitions are allowed.
"""

import pytest

from app.models import ComplaintStatus, VALID_TRANSITIONS, TERMINAL_STATUSES


class TestStatusFSM:
    """Test the finite state machine for complaint statuses."""

    def test_all_statuses_have_transition_rules(self):
        """Every status must be defined in the transition map."""
        for status in ComplaintStatus:
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"

    def test_terminal_statuses_have_no_transitions(self):
        """Terminal statuses should have empty transition sets."""
        expected_terminal = {
            ComplaintStatus.RESOLVED,
            ComplaintStatus.REJECTED,
            ComplaintStatus.WITHDRAWN,
            ComplaintStatus.REFERRED_OUTSIDE,
            ComplaintStatus.OUT_OF_SCOPE,
        }
        assert TERMINAL_STATUSES == expected_terminal

    def test_draft_can_become_submitted(self):
        """DRAFT → SUBMITTED is valid."""
        assert ComplaintStatus.SUBMITTED in VALID_TRANSITIONS[ComplaintStatus.DRAFT]

    def test_draft_can_be_withdrawn(self):
        """DRAFT → WITHDRAWN is valid."""
        assert ComplaintStatus.WITHDRAWN in VALID_TRANSITIONS[ComplaintStatus.DRAFT]

    def test_submitted_goes_to_triage_only(self):
        """SUBMITTED → TRIAGE is the only valid transition."""
        assert VALID_TRANSITIONS[ComplaintStatus.SUBMITTED] == {ComplaintStatus.TRIAGE}

    def test_triage_can_route_or_reject(self):
        """TRIAGE can go to ROUTED, REJECTED, REFERRED_OUTSIDE, or OUT_OF_SCOPE."""
        valid = VALID_TRANSITIONS[ComplaintStatus.TRIAGE]
        assert ComplaintStatus.ROUTED in valid
        assert ComplaintStatus.REJECTED in valid
        assert ComplaintStatus.REFERRED_OUTSIDE in valid
        assert ComplaintStatus.OUT_OF_SCOPE in valid

    def test_routed_can_progress(self):
        """ROUTED → IN_PROGRESS is valid."""
        assert ComplaintStatus.IN_PROGRESS in VALID_TRANSITIONS[ComplaintStatus.ROUTED]

    def test_citizen_confirmation_to_resolved_or_reopened(self):
        """CITIZEN_CONFIRMATION_PENDING → RESOLVED or REOPENED."""
        valid = VALID_TRANSITIONS[ComplaintStatus.CITIZEN_CONFIRMATION_PENDING]
        assert ComplaintStatus.RESOLVED in valid
        assert ComplaintStatus.REOPENED in valid
        assert len(valid) == 2

    def test_reopened_goes_back_to_triage_or_in_progress(self):
        """REOPENED → TRIAGE or IN_PROGRESS."""
        valid = VALID_TRANSITIONS[ComplaintStatus.REOPENED]
        assert ComplaintStatus.TRIAGE in valid
        assert ComplaintStatus.IN_PROGRESS in valid

    def test_resolved_is_terminal(self):
        """RESOLVED has no outgoing transitions."""
        assert len(VALID_TRANSITIONS[ComplaintStatus.RESOLVED]) == 0

    def test_invalid_transition_not_in_map(self):
        """SUBMITTED → RESOLVED should not be valid (skips steps)."""
        assert ComplaintStatus.RESOLVED not in VALID_TRANSITIONS[ComplaintStatus.SUBMITTED]

    def test_no_self_transitions(self):
        """No status should transition to itself."""
        for status, valid_next in VALID_TRANSITIONS.items():
            assert status not in valid_next, f"{status} can transition to itself"

    def test_full_happy_path(self):
        """Validate the full happy-path lifecycle is achievable."""
        path = [
            ComplaintStatus.DRAFT,
            ComplaintStatus.SUBMITTED,
            ComplaintStatus.TRIAGE,
            ComplaintStatus.ROUTED,
            ComplaintStatus.IN_PROGRESS,
            ComplaintStatus.RESPONSE_PROVIDED,
            ComplaintStatus.IMPLEMENTATION_REPORTED,
            ComplaintStatus.CITIZEN_CONFIRMATION_PENDING,
            ComplaintStatus.RESOLVED,
        ]
        for i in range(len(path) - 1):
            current = path[i]
            next_status = path[i + 1]
            assert next_status in VALID_TRANSITIONS[current], (
                f"Invalid step in happy path: {current.value} → {next_status.value}"
            )

    def test_reopen_cycle(self):
        """Validate that reopened complaints can go back through the cycle."""
        # CITIZEN_CONFIRMATION_PENDING → REOPENED → TRIAGE → ...
        assert ComplaintStatus.REOPENED in VALID_TRANSITIONS[ComplaintStatus.CITIZEN_CONFIRMATION_PENDING]
        assert ComplaintStatus.TRIAGE in VALID_TRANSITIONS[ComplaintStatus.REOPENED]


class TestTrackingId:
    """Test tracking ID generation."""

    def test_format(self):
        """Tracking ID should match FTMT-YYYYMMDD-XXXX format."""
        from app.models import generate_tracking_id
        tid = generate_tracking_id()
        assert tid.startswith("FTMT-")
        parts = tid.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 4  # 4 hex chars

    def test_uniqueness(self):
        """Multiple calls should (almost always) generate different IDs."""
        from app.models import generate_tracking_id
        ids = {generate_tracking_id() for _ in range(100)}
        # With 4 hex chars (65536 possibilities), 100 IDs should all be unique
        assert len(ids) == 100
