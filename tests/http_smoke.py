"""Smoke test for a running FlyMemory MCP endpoint (protocol level).

    python tests/http_smoke.py                                  # read-only
    python tests/http_smoke.py --write                          # + store/recall
    python tests/http_smoke.py --url http://127.0.0.1:8791/mcp  # explicit endpoint

The default endpoint is this plugin's private port (8791) or `FLYMEMORY_MCP_URL`.
Read-only by default: it handshakes with the official MCP client, lists the
tools, and reads statistics. With --write it stores one probe entry and recalls
it, so point it at a throwaway data directory unless you want that entry kept.

Exit code 0 means every checked step succeeded; 1 means a step failed.
"""
import argparse
import asyncio
import os
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_URL = os.environ.get("FLYMEMORY_MCP_URL", "http://127.0.0.1:8791/mcp")

EXPECTED_TOOLS = {
    "flymemory_remember", "flymemory_recall", "flymemory_stats",
    "flymemory_cleanup", "flymemory_forget", "flymemory_supersede",
    "flymemory_session_pack", "flymemory_consolidate",
    "flymemory_find_conflicts", "flymemory_insights",
    "flymemory_recall_index", "flymemory_get_memory",
    "flymemory_state_lookup", "flymemory_state_history", "flymemory_auto",
}


async def run(url: str, write: bool) -> int:
    failures = []
    started = time.perf_counter()
    async with streamablehttp_client(url) as (read, write_stream, _):
        async with ClientSession(read, write_stream) as session:
            await asyncio.wait_for(session.initialize(), timeout=15)
            print(f"[1] handshake OK in {time.perf_counter() - started:.2f}s")

            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            print(f"[2] tools({len(names)}): {', '.join(names)}")
            missing = EXPECTED_TOOLS - set(names)
            if missing:
                failures.append(f"missing tools: {sorted(missing)}")

            stats = await session.call_tool("flymemory_stats", {})
            print(f"[3] stats: {stats.content[0].text}")

            if write:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                text = f"dsh-flymemory smoke probe {stamp} — plugin copy write path"
                stored = await session.call_tool("flymemory_remember",
                                                 {"text": text, "tags": "smoke,dsh-plugin"})
                print(f"[4] remember: {stored.content[0].text}")
                if "REJECTED" in stored.content[0].text:
                    failures.append("remember was rejected")
                recalled = await session.call_tool("flymemory_recall",
                                                   {"query": "dsh-flymemory smoke probe plugin copy",
                                                    "top_k": 3})
                body = recalled.content[0].text
                print("[5] recall:\n" + body)
                if "smoke probe" not in body:
                    failures.append("recall did not return the probe entry")

    if failures:
        print("\nFAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("\nOK: endpoint behaves as expected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--write", action="store_true",
                        help="also store and recall one probe entry")
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.write))


if __name__ == "__main__":
    raise SystemExit(main())
