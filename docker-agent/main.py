import sys
from pathlib import Path

import typer

from agent import generate_compose, generate_dockerfile
from rag.ingest import ingest_folder

app = typer.Typer(add_completion=False)


@app.callback()
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        if len(sys.argv) >= 3 and sys.argv[1] not in {"--help", "-h"}:
            generate_dockerfile_command(sys.argv[1], sys.argv[2])
            raise typer.Exit()


@app.command("generate-dockerfile")
def generate_dockerfile_command(repo_path: str, output_path: str) -> None:
    dockerfile_content = generate_dockerfile(repo_path)
    output = Path(output_path)
    output.write_text(dockerfile_content, encoding="utf-8")
    typer.echo(f"Dockerfile written to {output}")


@app.command("generate-compose")
def generate_compose_command(repo_path: str, service_name: str, output_path: str) -> None:
    compose_content = generate_compose(repo_path, service_name)
    output = Path(output_path)
    output.write_text(compose_content, encoding="utf-8")
    typer.echo(f"Compose written to {output}")


@app.command("ingest-knowledge")
def ingest_knowledge_command(folder_path: str, collection_name: str, source_type: str) -> None:
    ingest_folder(folder_path, collection_name, source_type)


if __name__ == "__main__":
    app()
