import asyncio
import os
from pathlib import Path

import click

from .main import main


@click.command()
@click.option(
    "--env",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Instance directory with .static.yml, .dynamic.yml, sessions and storage.",
)
def cli(env: Path) -> None:
    """Run a Telegram Forwarder Bot instance."""
    # Configuration files and relative paths in them are resolved from here
    os.chdir(env)
    click.echo(f"Running instance from: {Path.cwd()}")
    asyncio.run(main())


if __name__ == "__main__":
    cli(prog_name="python -m telegram_forwarder_bot")
