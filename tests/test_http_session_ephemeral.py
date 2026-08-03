from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.compliance.mcp_client import MCPClient, REQUIRED_TOOLS


class EphemeralHTTPSessionTests(unittest.TestCase):
    def test_fresh_clients_can_exceed_retained_session_capacity(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        server_command = (
            "{python} -m coding_tools_mcp --workspace {workspace} "
            "--host 127.0.0.1 --port {port} --permission-mode trusted "
            "--http-session-mode ephemeral --http-session-max-sessions 4"
        )

        with patch.dict(
            os.environ,
            {"CODING_TOOLS_MCP_SERVER_CMD": server_command},
            clear=False,
        ):
            with MCPClient(workspace) as owner:
                session_ids = {str(owner.session_id)}
                for _ in range(12):
                    with MCPClient(workspace, url=owner.url) as sibling:
                        session_ids.add(str(sibling.session_id))
                        self.assertEqual(len(sibling.list_tools()), len(REQUIRED_TOOLS))

        self.assertEqual(len(session_ids), 13)


if __name__ == "__main__":
    unittest.main()
