from __future__ import annotations

import unittest

from coding_tools_mcp.errors import JsonRpcError
from coding_tools_mcp.mrtr import (
    MRTR_MAX_ROUNDS,
    seal_request_state,
    unseal_request_state,
    validate_input_responses,
)


class MRTRStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = b"x" * 32

    def token(self, *, round: int = 1) -> str:
        return seal_request_state(
            self.secret,
            tool_name="confirm",
            arguments={"project_id": "alpha", "value": "x"},
            state={"phase": "approval"},
            expected_responses={"confirm": "elicitation/create"},
            round=round,
            now=100.0,
        )

    def test_state_round_trip_is_bound_to_tool_arguments_and_expected_methods(self) -> None:
        state, round_value, expected = unseal_request_state(
            self.secret,
            self.token(),
            tool_name="confirm",
            arguments={"project_id": "alpha", "value": "x"},
            now=101.0,
        )

        self.assertEqual(state, {"phase": "approval"})
        self.assertEqual(round_value, 1)
        self.assertEqual(expected, {"confirm": "elicitation/create"})

    def test_state_rejects_expiry_and_argument_mismatch(self) -> None:
        with self.assertRaisesRegex(JsonRpcError, "expired"):
            unseal_request_state(
                self.secret,
                self.token(),
                tool_name="confirm",
                arguments={"project_id": "alpha", "value": "x"},
                now=701.0,
            )
        with self.assertRaisesRegex(JsonRpcError, "arguments"):
            unseal_request_state(
                self.secret,
                self.token(),
                tool_name="confirm",
                arguments={"project_id": "alpha", "value": "different"},
                now=101.0,
            )

    def test_response_keys_and_elicitation_shape_are_strict(self) -> None:
        expected = {"confirm": "elicitation/create"}
        validate_input_responses(
            expected,
            {"confirm": {"action": "accept", "content": {"confirmed": True}}},
        )
        with self.assertRaisesRegex(JsonRpcError, "keys"):
            validate_input_responses(expected, {"other": {"action": "decline"}})
        with self.assertRaisesRegex(JsonRpcError, "content"):
            validate_input_responses(expected, {"confirm": {"action": "accept"}})

    def test_round_limit_is_enforced_when_state_is_sealed(self) -> None:
        self.assertTrue(self.token(round=MRTR_MAX_ROUNDS))
        with self.assertRaisesRegex(ValueError, "round"):
            self.token(round=MRTR_MAX_ROUNDS + 1)


if __name__ == "__main__":
    unittest.main()
