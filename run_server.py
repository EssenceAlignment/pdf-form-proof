"""Entry point for the MCPB (desktop extension) bundle: runs the stdio MCP server."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from pdf_form_proof.server import main  # noqa: E402

if __name__ == "__main__":
    main()
