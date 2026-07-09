"""
CI/CD Orchestrator MCP Server — backward-compatible entry point.

The real implementation now lives in src/cicd_orchestrator/mcp_server.py so
it can be packaged and installed as a proper console script
(`cicd-orchestrator-mcp`), which lets it be run directly from the GitHub repo
with no local clone (mirroring how `npx -y <package>` works for Node MCP
servers):

    uvx --from git+https://github.com/kanchanachintala96/CICD.git cicd-orchestrator-mcp

This file is kept so existing local setups that point mcp.json at
`python mcp_server.py` continue to work unchanged.

Usage
-----
  python mcp_server.py          # stdio mode (for mcp.json / Claude Desktop)
  python mcp_server.py --http   # HTTP SSE mode on port 5001

Config (from .env or environment)
----------------------------------
  AZURE_DEVOPS_URL, AZURE_DEVOPS_PROJECT, AZURE_DEVOPS_PAT
  AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_ACR_NAME
  AZURE_APP_NAME, AZURE_LOCATION, AZURE_ACA_ENV_NAME
  AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from cicd_orchestrator.mcp_server import main

if __name__ == "__main__":
    main()
