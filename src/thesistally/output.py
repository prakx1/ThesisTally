"""Lossless script output for the terminal commands."""

import csv
import io
import json
from typing import Annotated

import typer

JSON = Annotated[bool, typer.Option("--json", help="Emit JSON.")]
CSV = Annotated[bool, typer.Option("--csv", help="Emit CSV with nested fields encoded as JSON.")]


def emit(data, as_json=False, as_csv=False, rows=None, columns=None):
    if as_json and as_csv:
        raise typer.BadParameter("Choose one output format.")
    if not as_csv:
        typer.echo(json.dumps(data, indent=2, allow_nan=False))
        return
    records = rows if rows is not None else data if isinstance(data, list) else [data]
    columns = columns or list(dict.fromkeys(k for r in records for k in r))
    stream = io.StringIO(newline="")
    w = csv.DictWriter(stream, fieldnames=columns)
    w.writeheader()
    for r in records:
        w.writerow(
            {
                k: json.dumps(v, allow_nan=False) if isinstance(v, (dict, list)) else v
                for k, v in r.items()
            }
        )
    typer.echo(stream.getvalue(), nl=False)
