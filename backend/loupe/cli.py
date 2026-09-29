"""Small operational CLI.

    python -m loupe.cli seed-demo        # load the synthetic demo repository
    python -m loupe.cli sync OWNER NAME  # one-off foreground sync (needs LOUPE_GITHUB_TOKEN)
"""

import argparse
import asyncio
import sys

from loupe.adapters import build_adapter
from loupe.config import get_settings
from loupe.db import init_engine, session_scope
from loupe.demo import DEMO_NAME, DEMO_OWNER, seed_demo
from loupe.logging_setup import configure_logging
from loupe.sync import get_or_create_repository, sync_repository


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loupe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed-demo", help="insert the synthetic demo repository")
    sp = sub.add_parser("sync", help="sync one repository in the foreground")
    sp.add_argument("owner")
    sp.add_argument("name")
    args = ap.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    init_engine(settings.database_url)

    if args.cmd == "seed-demo":
        with session_scope() as s:
            seed_demo(s)
        print(f"ok: {DEMO_OWNER}/{DEMO_NAME} is ready. Try: curl 'localhost:8000/api/v1/repos/{DEMO_OWNER}/{DEMO_NAME}/signals'")
        return 0

    if args.cmd == "sync":
        with session_scope() as s:
            repo, _ = get_or_create_repository(s, "github", args.owner, args.name)
            rid = repo.id
        adapter = build_adapter("github", settings)
        try:
            stats = asyncio.run(sync_repository(rid, adapter, settings.backfill_days))
        finally:
            asyncio.run(adapter.aclose())
        print(f"ok: {stats.as_dict()}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
