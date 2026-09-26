"""Start the BlendWMe MCP server:  python run.py --brain claude --workspace <folder>

This launcher lets the server run straight from its folder (inside the
installed add-on or next to the source) without installing it as a package.
"""

import os
import sys

# Put the folder ABOVE blendwme_mcp at the END of the search path: only the
# blendwme_mcp package itself is found there, never in place of an installed module.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blendwme_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
