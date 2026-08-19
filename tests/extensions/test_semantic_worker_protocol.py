from __future__ import annotations

import unittest

from coding_tools_mcp.extensions.semantic.protocol import (
    MAX_WORKER_MESSAGE_BYTES,
    SEMANTIC_OPERATIONS,
    WORKER_PROTOCOL_VERSION,
    WorkerProtocolError,
    decode_message,
    encode_message,
)


class SemanticWorkerProtocolTests(unittest.TestCase):
    def test_ready_round_trip_is_compact_newline_delimited_json(self) -> None:
        message = {
            "type": "ready",
            "protocol": WORKER_PROTOCOL_VERSION,
            "project_id": "app",
            "backend": "serena",
            "backend_version": "1.5.3",
            "languages": ["python"],
        }

        encoded = encode_message(message)

        self.assertTrue(encoded.endswith(b"\n"))
        self.assertFalse(encoded.endswith(b"\n\n"))
        self.assertNotIn(b": ", encoded)
        self.assertNotIn(b", ", encoded)
        self.assertEqual(decode_message(encoded), message)

    def test_request_round_trip_accepts_known_semantic_operation(self) -> None:
        message = {
            "type": "request",
            "protocol": 1,
            "id": "r1",
            "op": "list_symbols",
            "params": {"path": "a.py", "depth": 1, "max_results": 10},
        }

        self.assertEqual(decode_message(encode_message(message)), message)

    def test_protocol_exposes_exact_read_only_semantic_operation_set(self) -> None:
        self.assertEqual(
            SEMANTIC_OPERATIONS,
            frozenset(
                {
                    "list_symbols",
                    "find_symbol",
                    "find_definition",
                    "find_references",
                    "find_implementations",
                    "get_diagnostics",
                }
            ),
        )
        for operation in ("find_implementations", "get_diagnostics"):
            with self.subTest(operation=operation):
                message = {
                    "type": "request",
                    "protocol": 1,
                    "id": f"r-{operation}",
                    "op": operation,
                    "params": {"path": "a.py"},
                }
                self.assertEqual(decode_message(encode_message(message)), message)

    def test_success_response_requires_object_result(self) -> None:
        valid = {
            "type": "response",
            "protocol": 1,
            "id": "r1",
            "ok": True,
            "result": {"symbols": [], "truncated": False, "warnings": []},
        }
        invalid = {**valid, "result": []}

        self.assertEqual(decode_message(encode_message(valid)), valid)
        with self.assertRaisesRegex(WorkerProtocolError, "response.result"):
            decode_message(encode_message(invalid))

    def test_error_response_requires_structured_error_details(self) -> None:
        valid = {
            "type": "response",
            "protocol": 1,
            "id": "r1",
            "ok": False,
            "error": {
                "code": "SEMANTIC_FILE_UNSUPPORTED",
                "message": "unsupported file",
                "retryable": False,
                "details": {},
            },
        }
        invalid = {
            **valid,
            "error": {**valid["error"], "details": "not-an-object"},
        }

        self.assertEqual(decode_message(encode_message(valid)), valid)
        with self.assertRaisesRegex(WorkerProtocolError, "response.error.details"):
            decode_message(encode_message(invalid))

    def test_wrong_protocol_version_is_rejected(self) -> None:
        message = {
            "type": "request",
            "protocol": 2,
            "id": "r1",
            "op": "find_symbol",
            "params": {},
        }

        with self.assertRaisesRegex(WorkerProtocolError, "protocol"):
            decode_message(encode_message(message))

    def test_unknown_message_type_is_rejected(self) -> None:
        message = {"type": "mystery", "protocol": 1}

        with self.assertRaisesRegex(WorkerProtocolError, "message type"):
            decode_message(encode_message(message))

    def test_request_id_is_required(self) -> None:
        message = {
            "type": "request",
            "protocol": 1,
            "op": "find_symbol",
            "params": {},
        }

        with self.assertRaisesRegex(WorkerProtocolError, "request.id"):
            decode_message(encode_message(message))

    def test_unknown_operation_is_rejected(self) -> None:
        message = {
            "type": "request",
            "protocol": 1,
            "id": "r1",
            "op": "rename_symbol",
            "params": {},
        }

        with self.assertRaisesRegex(WorkerProtocolError, "request.op"):
            decode_message(encode_message(message))

    def test_request_params_must_be_object(self) -> None:
        message = {
            "type": "request",
            "protocol": 1,
            "id": "r1",
            "op": "find_symbol",
            "params": [],
        }

        with self.assertRaisesRegex(WorkerProtocolError, "request.params"):
            decode_message(encode_message(message))

    def test_oversized_line_is_rejected_before_json_decode(self) -> None:
        oversized = b"{" + (b"x" * MAX_WORKER_MESSAGE_BYTES) + b"}\n"

        with self.assertRaisesRegex(WorkerProtocolError, "exceeds"):
            decode_message(oversized)

    def test_encode_rejects_oversized_message(self) -> None:
        message = {
            "type": "request",
            "protocol": 1,
            "id": "r1",
            "op": "find_symbol",
            "params": {"query": "x" * MAX_WORKER_MESSAGE_BYTES},
        }

        with self.assertRaisesRegex(WorkerProtocolError, "exceeds"):
            encode_message(message)

    def test_malformed_utf8_and_json_are_rejected(self) -> None:
        with self.assertRaisesRegex(WorkerProtocolError, "UTF-8"):
            decode_message(b"\xff\n")
        with self.assertRaisesRegex(WorkerProtocolError, "JSON"):
            decode_message(b"{not-json}\n")


if __name__ == "__main__":
    unittest.main()
