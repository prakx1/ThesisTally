"""Historical strategy plugin discovery and signed research samples."""

from importlib import metadata
from pathlib import Path
from typing import Protocol

import pandas as pd

from .discovery_registry import digest, encoded, identifier, write_once

BUILTINS = ("pullback", "b10", "b5")


class StrategyPlugin(Protocol):
    """Return realized equal-risk opportunities and the dated window coverage metadata."""

    def load(self, archive: Path) -> tuple[pd.DataFrame, dict]: ...


def plugins():
    loaded = {}
    for entry in metadata.entry_points(group="thesistally.discovery_strategies"):
        name = identifier(entry.name)
        if name in loaded or name in BUILTINS:
            raise ValueError(f"Duplicate discovery strategy {name}")
        loaded[name] = entry
    return loaded


def parquet_bytes(frame):
    import io

    buffer = io.BytesIO()
    frame = frame.copy()
    frame.attrs.clear()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def store_sample(root, name, frame, windows, provenance, plans=None):
    raw = parquet_bytes(frame)
    folder = root / "samples" / name
    write_once(folder / "trades.parquet", raw)
    info = {"strategy": name, "sha256": digest(raw), "windows": windows, "provenance": provenance}
    if plans is not None:
        payload = parquet_bytes(plans)
        write_once(folder / "plans.parquet", payload)
        info["plans_sha256"] = digest(payload)
    write_once(folder / "receipt.json", encoded(info))


def sample(root, archive, name):
    import io
    import json

    name = identifier(name)
    folder = root / "samples" / name
    available = plugins()
    if name not in BUILTINS and name not in available:
        raise ValueError("Unknown strategy. Available: " + ", ".join([*BUILTINS, *available]))
    if not (folder / "receipt.json").is_file():
        if name in BUILTINS:
            from . import discovery_features, discovery_trades

            raw, windows = discovery_trades.build(archive)
            provenance = dict(raw.attrs.get("provenance", {}))
            plans = discovery_trades.build_plans(archive)
            combined = pd.concat(
                [raw.assign(_row_kind="trade"), plans.assign(_row_kind="plan")],
                ignore_index=True,
            )
            enriched = discovery_features.enrich(combined, archive)
            if len(enriched) != len(combined):
                raise ValueError("Descriptor join changed the population")
            provenance.update(enriched.attrs.get("provenance", {}))
            for module in (discovery_features, discovery_trades):
                provenance[module.__name__] = digest(Path(module.__file__).read_bytes())
            for strategy in BUILTINS:
                group = enriched.loc[enriched.strategy == strategy]
                part = group.loc[
                    group._row_kind == "trade",
                    raw.columns.union(enriched.columns.difference(combined.columns), sort=False),
                ].copy()
                # enrich may rebuild an existing field in its original column.
                for col in ("mature", "counted"):
                    part[col] = part[col].astype(bool)
                planned = group.loc[group._row_kind == "plan"].drop(columns="_row_kind")
                if not planned.empty:
                    planned = planned.dropna(axis=1, how="all").copy()
                    planned["blocked_data"] = planned.blocked_data.astype(bool)
                if part.empty:
                    raise ValueError(f"Missing historical strategy sample: {strategy}")
                store_sample(
                    root,
                    strategy,
                    part,
                    windows[strategy],
                    provenance,
                    planned if not planned.empty else None,
                )
        else:
            plugin: StrategyPlugin = available[name].load()()
            frame, windows = plugin.load(archive)
            plan_loader = getattr(plugin, "plans", None)
            plans = plan_loader(archive) if plan_loader else None
            store_sample(root, name, frame, windows, frame.attrs.get("provenance", {}), plans)
    raw = (folder / "trades.parquet").read_bytes()
    info = json.loads((folder / "receipt.json").read_bytes())
    if info["strategy"] != name or info["sha256"] != digest(raw):
        raise ValueError(f"Invalid discovery sample receipt for {name}")
    return pd.read_parquet(io.BytesIO(raw)), info
