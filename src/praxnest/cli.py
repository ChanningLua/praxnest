"""praxnest CLI — `praxnest {serve, init, version}`.

We deliberately mirror praxdaily's command surface (serve, --cwd,
--port, --host) so users coming from praxdaily don't have to relearn.
The actual feature delta is what's INSIDE the served app — the CLI
shell is the same shape.
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="praxnest")
    parser.add_argument(
        "--version",
        action="version",
        version=f"praxnest {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser(
        "serve",
        help="Start the local web app (defaults to http://127.0.0.1:7878)",
    )
    p_serve.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address. Use 0.0.0.0 to expose on the local network for team use.",
    )
    p_serve.add_argument("--port", type=int, default=7878)
    p_serve.add_argument(
        "--no-open", action="store_true", help="Don't open the browser",
    )
    p_serve.add_argument(
        "--data-dir", default=None,
        help="Where to store the SQLite db + uploaded notes. Defaults to ./.praxnest/",
    )

    p_init = sub.add_parser(
        "init",
        help="Initialize a workspace: creates SQLite schema + an admin user",
    )
    p_init.add_argument(
        "--data-dir", default=None,
        help="Where to write the workspace (defaults to ./.praxnest/)",
    )
    p_init.add_argument(
        "--admin-username", default="admin",
        help="Username for the first admin user (default: admin)",
    )
    p_init.add_argument(
        "--admin-password", default=None,
        help="Password for the admin user. If omitted, you'll be prompted (recommended).",
    )

    return parser


def _resolve_data_dir(arg: str | None) -> Path:
    """Resolve --data-dir or fall back to ./.praxnest/.

    Why under cwd by default: keeps a workspace scoped to the project
    you cd'd into, mirroring praxdaily's `.prax/` convention. Easy to
    git-ignore, easy to back up.
    """
    if arg:
        return Path(arg).resolve()
    return (Path.cwd() / ".praxnest").resolve()


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "serve":
        from .app import serve

        data_dir = _resolve_data_dir(args.data_dir)
        url = f"http://{args.host}:{args.port}/"
        print(f"praxnest {__version__} → {url}")
        print(f"data: {data_dir}")
        if args.host == "0.0.0.0":
            print("(team mode: bound on 0.0.0.0 — anyone on your LAN can reach this)")
        print(f"(Ctrl+C to stop)")

        if not args.no_open and args.host != "0.0.0.0":
            try:
                webbrowser.open(url)
            except Exception:
                pass

        serve(host=args.host, port=args.port, data_dir=data_dir)
        return

    if args.command == "init":
        from . import db, auth

        data_dir = _resolve_data_dir(args.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        db.initialize(data_dir)

        password = args.admin_password
        if not password:
            import getpass
            password = getpass.getpass(f"Password for admin user {args.admin_username!r}: ")
            confirm = getpass.getpass("Confirm: ")
            if password != confirm:
                print("Passwords don't match. Aborting.")
                sys.exit(2)

        try:
            user_id = auth.create_user(data_dir, username=args.admin_username, password=password, role="admin")
        except auth.UserAlreadyExists:
            print(f"User {args.admin_username!r} already exists. Use a different name or delete the existing db.")
            sys.exit(1)

        print(f"✓ Initialized workspace at {data_dir}")
        print(f"✓ Created admin user {args.admin_username!r} (id={user_id})")
        print(f"  → Run `praxnest serve` and log in.")
        return

    raise SystemExit(1)


if __name__ == "__main__":
    main()
