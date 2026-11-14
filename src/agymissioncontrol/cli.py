"""Typer CLI entrypoint for agyMissionControl."""

import typer

import agymissioncontrol

app = typer.Typer(
    name="agymissioncontrol",
    help="Autonomous task orchestrator, quota governor, and issue-to-spec compiler for agy.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(agymissioncontrol.__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the agymissioncontrol version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """agyMissionControl CLI root callback."""
    _ = version


if __name__ == "__main__":
    app()
