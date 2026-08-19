from __future__ import annotations

import json
import unittest

from coding_tools_mcp.extensions.semantic import SEMANTIC_BACKEND
from coding_tools_mcp.extensions.semantic.backend import SemanticBackendError
from coding_tools_mcp.extensions.semantic.model import (
    FindDefinitionRequest,
    FindDefinitionResult,
    FindImplementationsRequest,
    FindImplementationsResult,
    FindReferencesRequest,
    FindReferencesResult,
    FindSymbolRequest,
    FindSymbolResult,
    GetDiagnosticsRequest,
    GetDiagnosticsResult,
    ListSymbolsRequest,
    ListSymbolsResult,
    SemanticDiagnostic,
    SemanticPosition,
    SemanticRange,
    SemanticReference,
    SemanticSymbol,
)


class SemanticModelTests(unittest.TestCase):
    def test_symbol_payload_is_backend_neutral_and_json_safe(self) -> None:
        symbol = SemanticSymbol(
            name="hello",
            name_path="Greeter/hello",
            kind="method",
            path="src/sample.py",
            range=SemanticRange(
                start=SemanticPosition(line=2, column=5),
                end=SemanticPosition(line=3, column=33),
            ),
            children=(),
        )

        payload = symbol.payload()

        self.assertEqual(
            payload,
            {
                "name": "hello",
                "name_path": "Greeter/hello",
                "kind": "method",
                "path": "src/sample.py",
                "range": {
                    "start": {"line": 2, "column": 5},
                    "end": {"line": 3, "column": 33},
                },
                "children": [],
            },
        )
        json.dumps(payload)
        self.assertNotIn("serena", json.dumps(payload).lower())

    def test_optional_body_is_bounded_metadata_not_backend_blob(self) -> None:
        symbol = SemanticSymbol(
            name="f",
            name_path="f",
            kind="function",
            path="a.py",
            range=SemanticRange(
                start=SemanticPosition(1, 1),
                end=SemanticPosition(1, 14),
            ),
            body="def f(): pass",
            body_truncated=True,
        )

        payload = symbol.payload()

        self.assertEqual(payload["body"], "def f(): pass")
        self.assertIs(payload["body_truncated"], True)

    def test_reference_payload_includes_range_and_containing_symbol(self) -> None:
        containing = SemanticSymbol.summary(
            name="run",
            name_path="run",
            kind="function",
            path="src/sample.py",
        )
        reference = SemanticReference(
            path="src/sample.py",
            range=SemanticRange(
                start=SemanticPosition(10, 12),
                end=SemanticPosition(10, 19),
            ),
            containing_symbol=containing,
        )

        payload = reference.payload()

        self.assertEqual(payload["containing_symbol"]["name_path"], "run")
        self.assertEqual(payload["range"]["start"], {"line": 10, "column": 12})

    def test_positions_are_one_based(self) -> None:
        for line, column in ((0, 1), (1, 0), (-1, 1), (1, -1)):
            with self.subTest(line=line, column=column):
                with self.assertRaisesRegex(ValueError, "one-based"):
                    SemanticPosition(line, column)

    def test_request_defaults_match_public_contract(self) -> None:
        self.assertEqual(ListSymbolsRequest(path="a.py"), ListSymbolsRequest("a.py", 1, 500))
        self.assertEqual(FindSymbolRequest(query="A"), FindSymbolRequest("A", "", False, 50))
        self.assertEqual(FindDefinitionRequest("a.py", 3, 4).line, 3)
        self.assertEqual(FindReferencesRequest("a.py", 3, 4).max_results, 500)
        self.assertFalse(FindReferencesRequest("a.py", 3, 4).include_declaration)
        self.assertEqual(FindImplementationsRequest("a.py", 3, 4).max_results, 200)
        self.assertEqual(GetDiagnosticsRequest("a.py").min_severity, "hint")
        self.assertEqual(GetDiagnosticsRequest("a.py").max_results, 500)

    def test_diagnostic_request_validates_one_based_ranges_and_closed_severity(self) -> None:
        with self.assertRaisesRegex(ValueError, "one-based"):
            GetDiagnosticsRequest("a.py", start_line=0)
        with self.assertRaisesRegex(ValueError, "precedes"):
            GetDiagnosticsRequest("a.py", start_line=4, end_line=3)
        with self.assertRaisesRegex(ValueError, "severity"):
            GetDiagnosticsRequest("a.py", min_severity="critical")
        with self.assertRaisesRegex(ValueError, "severity"):
            SemanticDiagnostic(
                path="a.py",
                range=SemanticRange(SemanticPosition(1, 1), SemanticPosition(1, 2)),
                severity="unknown",
                message="unsupported",
            )

    def test_result_payloads_use_normalized_collections(self) -> None:
        symbol = SemanticSymbol.summary(name="A", name_path="A", kind="class", path="a.py")
        reference = SemanticReference(
            path="a.py",
            range=SemanticRange(SemanticPosition(2, 1), SemanticPosition(2, 2)),
        )

        self.assertEqual(
            ListSymbolsResult((symbol,), truncated=True, warnings=("limited",)).payload(),
            {"symbols": [symbol.payload()], "truncated": True, "warnings": ["limited"]},
        )
        self.assertEqual(FindSymbolResult((symbol,)).payload()["symbols"], [symbol.payload()])
        self.assertEqual(
            FindDefinitionResult((symbol,)).payload()["definitions"],
            [symbol.payload()],
        )
        self.assertEqual(
            FindReferencesResult((reference,)).payload()["references"],
            [reference.payload()],
        )
        diagnostic = SemanticDiagnostic(
            path="a.py",
            range=SemanticRange(SemanticPosition(3, 2), SemanticPosition(3, 8)),
            severity="warning",
            message="example",
            code="W1",
            source="test",
        )
        self.assertEqual(
            FindImplementationsResult((symbol,)).payload()["implementations"],
            [symbol.payload()],
        )
        self.assertEqual(
            GetDiagnosticsResult((diagnostic,)).payload()["diagnostics"],
            [diagnostic.payload()],
        )

    def test_backend_error_and_capability_are_stable(self) -> None:
        error = SemanticBackendError(
            "SEMANTIC_TIMEOUT",
            "timed out",
            retryable=True,
            details={"worker": "alpha"},
        )

        self.assertEqual(error.code, "SEMANTIC_TIMEOUT")
        self.assertEqual(error.message, "timed out")
        self.assertTrue(error.retryable)
        self.assertEqual(error.details, {"worker": "alpha"})
        self.assertEqual(SEMANTIC_BACKEND.name, "semantic.backend")


if __name__ == "__main__":
    unittest.main()
