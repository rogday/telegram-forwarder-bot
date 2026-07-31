#!/usr/bin/env python3
"""Entry point for running a Telegram Forwarder Bot instance."""

import asyncio
import os
import sys
from pathlib import Path

import click

# Project root is where this script lives
_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_PATH = _PROJECT_ROOT / "src"


@click.command()
@click.option(
    "--env",
    required=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    help="Path to the instance directory containing YAML configuration files.",
)
def main(env: Path) -> None:
    """Run a Telegram Forwarder Bot instance.

    The --env flag points to an instance directory that contains:
    - .static.yml with credentials and static settings
    - .dynamic.yml with runtime-reloadable settings
    - Session files (user.session, bot.session)
    - Storage database (storage.db)
    """
    if not env.is_dir():
        click.echo(f"Error: '{env}' is not a directory.")
        sys.exit(1)

    if not _SRC_PATH.exists():
        click.echo(f"Error: Source directory '{_SRC_PATH}' does not exist.")
        sys.exit(1)

    # Resolve instance dir BEFORE os.chdir (paths change after chdir)
    instance_dir = env.resolve()

    # Change to instance directory so config.py finds the configuration files
    os.chdir(instance_dir)

    # Add src/ to path for imports
    sys.path.insert(0, str(_SRC_PATH))

    from main import main as bot_main

    click.echo(f"Running instance from: {instance_dir}")
    asyncio.run(bot_main())


if __name__ == "__main__":
    main()
