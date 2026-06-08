from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from bwli import __version__
from bwli.client import BwClient
from bwli.config import BwConnectionConfig, ConfigError
from bwli.field_lineage import (
    FieldOutputFormat,
    SqlOutputFormat,
    load_text,
    parse_native_sql_view,
    parse_transformation_mapping_xml,
    render_field_lineage,
    render_sql_view_evidence,
)
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
from bwli.live import collect_live_snapshot
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
    collect.add_argument(
        "--confirm-read-only",
        action="store_true",
        help="Required with --live to explicitly confirm read-only BW calls.",
    )
    collect.add_argument(
        "--search-term",
        dest="search_terms",
        action="append",
        default=[],
        help="BW search term to collect; repeatable.",
    )
    collect.add_argument(
        "--object",
        dest="objects",
        action="append",
        default=[],
        help="BW object name to collect dataflow/xref; repeatable.",
    )
    collect.add_argument(
        "--object-type",
        default="ADSO",
        help="BW object type for dataflow calls, e.g. ADSO/HCPR/RSDS.",
    )
    collect.add_argument("--source-system", help="Required for RSDS dataflow object names.")
    collect.add_argument(
        "--dataflow-direction",
        choices=["upwards", "downwards", "both"],
        default="downwards",
        help="Direction depth for live dataflow calls.",
    )
    collect.add_argument("--dataflow-levels", type=int, default=3, help="Dataflow traversal depth.")
    collect.add_argument(
        "--xref-direction",
        choices=["upstream", "downstream"],
        default="downstream",
        help="Xref direction for live collection.",
    )
    collect.add_argument(
        "--skip-dataflow",
        action="store_true",
        help="Skip live dataflow calls for --object values.",
    )
    collect.add_argument(
        "--skip-xref",
        action="store_true",
        help="Skip live xref calls for --object values.",
    )

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

    field_lineage = subparsers.add_parser(
        "field-lineage",
        help="Parse local Transformation XML field mapping evidence.",
    )
    field_lineage.add_argument("--xml", type=Path, required=True, help="Transformation XML file.")
    field_lineage.add_argument("--transformation-id", required=True, help="Transformation id.")
    field_lineage.add_argument("--source-object", required=True, help="Source object id.")
    field_lineage.add_argument("--target-object", required=True, help="Target object id.")
    field_lineage.add_argument(
        "--format",
        choices=["json", "md"],
        default="json",
        help="Output format.",
    )
    field_lineage.add_argument("--out", type=Path, help="Optional output path.")

    sql_view = subparsers.add_parser(
        "sql-view",
        help="Parse local Native SQL View SQL text into deterministic evidence.",
    )
    sql_view.add_argument("--id", required=True, help="Native SQL View id.")
    sql_view.add_argument("--sql-file", type=Path, required=True, help="SQL definition file.")
    sql_view.add_argument(
        "--format",
        choices=["json", "md"],
        default="json",
        help="Output format.",
    )
    sql_view.add_argument("--out", type=Path, help="Optional output path.")

    subparsers.add_parser("report", help="report command placeholder for later milestones.")

    serve = subparsers.add_parser("serve", help="Run the local backend API and built frontend.")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host; keep loopback by default.")
    serve.add_argument("--port", type=int, default=8787, help="Bind port.")
    serve.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root used to resolve local graph/report files.",
    )
    serve.add_argument(
        "--static-dir",
        type=Path,
        default=Path("web/dist"),
        help="Built frontend directory to serve when present.",
    )
    serve.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn reload for development.",
    )

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
    if command == "field-lineage":
        return _field_lineage(args)
    if command == "sql-view":
        return _sql_view(args)
    if command == "report":
        print(f"{command} {SAFE_STUB_MESSAGE}")
        return 0
    if command == "serve":
        return _serve(args)

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
        if not args.confirm_read_only:
            print(
                "live collection requires --confirm-read-only; no BW calls were made",
                file=sys.stderr,
            )
            return 2
        try:
            out_dir = _resolve_live_output_dir(Path.cwd(), args.out)
        except ValueError as exc:
            print(f"{exc}; no BW calls were made", file=sys.stderr)
            return 2
        try:
            config = BwConnectionConfig.from_env()
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        def factory() -> BwClient:
            return BwClient(
                base_url=config.url,
                username=config.user,
                password=config.password.get_secret_value(),
                sap_client=config.client,
                language=config.language,
                verify=config.httpx_verify_arg(),
                trust_env=config.trust_env,
            )

        try:
            manifest = collect_live_snapshot(
                out_dir=out_dir,
                client_factory=factory,
                search_terms=args.search_terms,
                object_names=args.objects,
                include_dataflow=not args.skip_dataflow,
                include_xref=not args.skip_xref,
                xref_direction=args.xref_direction,
                dataflow_object_type=args.object_type,
                dataflow_source_system=args.source_system,
                dataflow_direction=args.dataflow_direction,
                dataflow_levels=args.dataflow_levels,
            )
        except Exception as exc:
            print(f"live collection failed: {type(exc).__name__}", file=sys.stderr)
            return 1
        print(f"wrote {out_dir / 'manifest.json'} with {len(manifest.payloads)} payload(s)")
        return 0
    print(SAFE_STUB_MESSAGE)
    return 0


def _lineage(args: argparse.Namespace) -> int:
    graph = load_graph(args.graph)
    result = graph.traverse(args.object, direction=args.direction, max_depth=args.max_depth)
    rendered = render_lineage(result, output_format=args.format)
    return _write_or_print(rendered, args.out)


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
    return _write_or_print(rendered, args.out)


def _diff(args: argparse.Namespace) -> int:
    if args.before is None or args.after is None:
        if args.before is not None or args.after is not None:
            print("diff requires both --before and --after", file=sys.stderr)
            return 2
        print(f"diff {SAFE_STUB_MESSAGE}")
        return 0
    diff = diff_graphs(load_graph(args.before), load_graph(args.after))
    rendered = render_snapshot_diff(diff)
    return _write_or_print(rendered, args.out)


def _field_lineage(args: argparse.Namespace) -> int:
    document = parse_transformation_mapping_xml(
        load_text(args.xml),
        transformation_id=args.transformation_id,
        source_object_id=args.source_object,
        target_object_id=args.target_object,
    )
    rendered = render_field_lineage(document, output_format=cast(FieldOutputFormat, args.format))
    return _write_or_print(rendered, args.out)


def _sql_view(args: argparse.Namespace) -> int:
    result = parse_native_sql_view(load_text(args.sql_file), view_id=args.id)
    rendered = render_sql_view_evidence(result, output_format=cast(SqlOutputFormat, args.format))
    return _write_or_print(rendered, args.out)


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ["BWLI_PROJECT_ROOT"] = str(args.project_root.resolve())
    os.environ["BWLI_STATIC_DIR"] = str(args.static_dir)
    uvicorn.run(
        "bwli.server:create_default_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _write_or_print(rendered: str, out: Path | None) -> int:
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(rendered, end="")
    return 0


def _resolve_live_output_dir(root: Path, out_dir: Path) -> Path:
    root_resolved = root.resolve()
    resolved = out_dir if out_dir.is_absolute() else root_resolved / out_dir
    resolved = resolved.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("live output path is outside project root") from exc
    return resolved
