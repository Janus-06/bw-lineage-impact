from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from bwli import __version__
from bwli.graph import Direction
from bwli.impact import (
    ImpactOutputFormat,
    diff_graphs,
    load_changes,
    render_impact_report,
    render_snapshot_diff,
    run_impact_analysis,
)
from bwli.lineage import load_graph, render_lineage
from bwli.snapshot import write_fixture_snapshot

SAFE_STUB_MESSAGE = "offline stub: no BW calls were made"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bwli",
        description="Local-first read-only BW lineage and change-impact analyzer.",
    )
    parser.add_argument("--version", action="store_true", help="Print bwli version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    collect = subparsers.add_parser("collect", help="Collect a local snapshot or gated live data.")
    collect.add_argument("--fixture", type=Path, help="Local JSON fixture to snapshot offline.")
    collect.add_argument(
        "--out",
        type=Path,
        default=Path(".tmp/snapshot"),
        help="Output snapshot directory.",
    )
    collect.add_argument("--live", action="store_true", help="Enable gated live collection path.")

    lineage = subparsers.add_parser("lineage", help="Traverse a local graph snapshot.")
    lineage.add_argument("--graph", type=Path, required=True, help="Graph JSON file to read.")
    lineage.add_argument("--object", required=True, help="Start node/object id.")
    lineage.add_argument(
        "--direction",
        choices=[item.value for item in Direction],
        default=Direction.DOWNSTREAM.value,
        help="Traversal direction.",
    )
    lineage.add_argument("--max-depth", type=int, default=3, help="Traversal depth cap.")
    lineage.add_argument(
        "--format",
        choices=["json", "mermaid", "md"],
        default="json",
        help="Output format.",
    )
    lineage.add_argument("--out", type=Path, help="Optional output path.")

    impact = subparsers.add_parser("impact", help="Analyze local graph impact from a change file.")
    impact.add_argument("--graph", type=Path, help="Graph JSON file to read.")
    impact.add_argument("--changes", type=Path, help="Manual change JSON file to read.")
    impact.add_argument("--max-depth", type=int, default=3, help="Downstream traversal depth cap.")
    impact.add_argument(
        "--format",
        choices=["json", "md"],
        default="json",
        help="Output format.",
    )
    impact.add_argument("--out", type=Path, help="Optional output path.")

    diff = subparsers.add_parser("diff", help="Diff two local graph snapshots.")
    diff.add_argument("--before", type=Path, help="Before graph JSON file.")
    diff.add_argument("--after", type=Path, help="After graph JSON file.")
    diff.add_argument("--out", type=Path, help="Optional output path.")

    subparsers.add_parser("report", help="report command placeholder for later milestones.")

    return parser


def app(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    if args.version:
        print(f"bwli {__version__}")
        return 0

    command = args.command
    if command is None:
        print("bwli: local-first read-only analyzer. Use --help for commands.")
        return 0

    if command == "collect":
        return _collect(args)
    if command == "lineage":
        return _lineage(args)
    if command == "impact":
        return _impact(args)
    if command == "diff":
        return _diff(args)
    if command == "report":
        print(f"{command} {SAFE_STUB_MESSAGE}")
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


def _collect(args: argparse.Namespace) -> int:
    fixture: Path | None = args.fixture
    live: bool = args.live
    if fixture is not None:
        manifest = write_fixture_snapshot(fixture, args.out)
        print(f"wrote {args.out / 'manifest.json'} with {len(manifest.payloads)} payload(s)")
        return 0
    if live:
        if os.environ.get("BWLI_LIVE") != "1":
            print(
                "live collection is gated: set BWLI_LIVE=1; no BW calls were made",
                file=sys.stderr,
            )
            return 2
        print("live collection placeholder enabled; no BW calls were made in this stub")
        return 0
    print(SAFE_STUB_MESSAGE)
    return 0


def _lineage(args: argparse.Namespace) -> int:
    graph = load_graph(args.graph)
    result = graph.traverse(args.object, direction=args.direction, max_depth=args.max_depth)
    rendered = render_lineage(result, output_format=args.format)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(rendered, end="")
    return 0


def _impact(args: argparse.Namespace) -> int:
    if args.graph is None or args.changes is None:
        if args.graph is not None or args.changes is not None:
            print("impact requires both --graph and --changes", file=sys.stderr)
            return 2
        print(f"impact {SAFE_STUB_MESSAGE}")
        return 0
    graph = load_graph(args.graph)
    changes = load_changes(args.changes)
    report = run_impact_analysis(graph, changes, max_depth=args.max_depth)
    rendered = render_impact_report(report, output_format=cast(ImpactOutputFormat, args.format))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(rendered, end="")
    return 0


def _diff(args: argparse.Namespace) -> int:
    if args.before is None or args.after is None:
        if args.before is not None or args.after is not None:
            print("diff requires both --before and --after", file=sys.stderr)
            return 2
        print(f"diff {SAFE_STUB_MESSAGE}")
        return 0
    diff = diff_graphs(load_graph(args.before), load_graph(args.after))
    rendered = render_snapshot_diff(diff)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(rendered, end="")
    return 0
