import typer

app = typer.Typer()


@app.command()
def submit(
    job_file: str = typer.Argument(..., help="Path to job YAML file"),
    syncthing_root: str = typer.Option(..., help="Path to Syncthing shared folder"),
):
    """Submit a job for execution."""
    typer.echo(f"Submitting job from {job_file}")
    typer.echo(f"Using Syncthing root: {syncthing_root}")
    # TODO: Implement job submission


@app.command()
def list():
    """List all jobs."""
    typer.echo("Listing jobs...")
    # TODO: Implement job listing


@app.command()
def logs(
    job_id: str = typer.Argument(..., help="Job ID to show logs for"),
    follow: bool = typer.Option(False, help="Follow logs output"),
):
    """Show logs for a job."""
    typer.echo(f"Showing logs for job {job_id}")
    if follow:
        typer.echo("Following logs...")
    # TODO: Implement log fetching


if __name__ == "__main__":
    app()
