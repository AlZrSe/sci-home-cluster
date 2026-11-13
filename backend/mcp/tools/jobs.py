"""MCP tools for job management."""

from shared.schemas.job_spec import JobSpec
from backend.services.job_service import JobService


async def jobs_list(ctx, args: dict) -> dict:
    service = JobService()
    jobs, total = await service.list_jobs(
        status=args.get("status"),
        node_id=args.get("node_id"),
        search=args.get("search"),
        limit=int(args.get("limit", 10)),
        offset=int(args.get("offset", 0)),
    )
    return {
        "items": [j.model_dump(mode="json") for j in jobs],
        "total": total,
    }


async def jobs_get(ctx, args: dict) -> dict:
    service = JobService()
    job = await service.get_job(args["job_id"])
    if job is None:
        raise ValueError("job_not_found")
    return job.model_dump(mode="json")


async def jobs_create(ctx, args: dict) -> dict:
    spec_data = args["spec"]
    if isinstance(spec_data, dict):
        spec = JobSpec(**spec_data)
    else:
        spec = spec_data  # type: ignore[assignment]
    service = JobService()
    created = await service.submit_job(spec)
    return created.model_dump(mode="json")


async def jobs_delete(ctx, args: dict) -> dict:
    service = JobService()
    await service.delete_job(args["job_id"])
    return {"deleted": True}


async def jobs_retry(ctx, args: dict) -> dict:
    service = JobService()
    result = await service.retry_job(args["job_id"])
    return result.model_dump(mode="json")


async def jobs_cancel(ctx, args: dict) -> dict:
    service = JobService()
    issued_by = args.get("issued_by") or ctx.subject
    result = await service.cancel_job(args["job_id"], issued_by=issued_by)
    if result is None:
        existing = await service.get_job(args["job_id"])
        if existing is None:
            raise ValueError("job_not_found")
        raise ValueError("job_not_cancellable")
    return result.model_dump(mode="json")


async def jobs_metrics(ctx, args: dict) -> dict:
    service = JobService()
    metrics = await service.get_job_metrics(args["job_id"])
    if metrics is None:
        raise ValueError("metrics_not_found")
    return metrics.model_dump(mode="json")


async def jobs_logs(ctx, args: dict) -> list:
    service = JobService()
    logs = await service.get_job_logs(args["job_id"])
    if logs is None:
        raise ValueError("logs_not_found")
    result = []
    for entry in logs:
        result.append(entry.model_dump(mode="json"))
    return result


async def jobs_logs_history(ctx, args: dict) -> list:
    return await jobs_logs(ctx, args)


async def jobs_logs_stream(ctx, args: dict) -> dict:
    service = JobService()
    job_id = args["job_id"]
    job = await service.get_job(job_id)
    if job is None:
        raise ValueError("job_not_found")
    available = service.log_stream_available(job_id)
    logs = await service.get_job_logs(job_id)
    if logs is None:
        logs = []
    cursor = int(args.get("cursor") or 0)
    page_size = 100
    window = logs[cursor : cursor + page_size]
    lines = [entry.line if hasattr(entry, "line") else str(entry) for entry in window]
    next_cursor: "int | None" = cursor + len(lines)
    if next_cursor is not None and next_cursor >= len(logs):
        next_cursor = None
    return {
        "job_id": job_id,
        "available": available,
        "lines": lines,
        "cursor": cursor,
        "next_cursor": next_cursor,
        "total": len(logs),
    }


async def jobs_commands_list(ctx, args: dict) -> dict:  # noqa: C901
    service = JobService()
    job = await service.get_job(args["job_id"])
    if job is None:
        raise ValueError("job_not_found")
    # Reuse route logic? Import from syncthing service command log
    from backend.core.config import settings
    from backend.services.job_folder import (
        commands_dir,
        acks_dir,
        command_file,
        ack_file,
    )
    from shared.file_ops.yaml_utils import read_yaml
    from shared.schemas.job_command import JobCommand
    from shared.schemas.job_command_ack import JobCommandAck

    root = settings.SYNCTHING_ROOT
    items = []
    command_ids = set()
    cdir = commands_dir(root, args["job_id"])
    adir = acks_dir(root, args["job_id"])
    if cdir.exists():
        for f in cdir.glob("*.yaml"):
            command_ids.add(f.stem)
    if adir.exists():
        for f in adir.glob("*.yaml"):
            command_ids.add(f.stem)
    for cid in sorted(command_ids):
        cmd = None
        ack = None
        cf = command_file(root, args["job_id"], cid)
        if cf.exists():
            try:
                cmd = read_yaml(str(cf), JobCommand)
            except Exception:
                cmd = None
        af = ack_file(root, args["job_id"], cid)
        if af.exists():
            try:
                ack = read_yaml(str(af), JobCommandAck)
            except Exception:
                ack = None
        items.append(
            {
                "command": cmd.model_dump(mode="json") if cmd is not None else None,
                "ack": ack.model_dump(mode="json") if ack is not None else None,
            }
        )
    return {"items": items, "total": len(items)}


def register(server):
    from backend.mcp.server import MCPServer  # noqa
    from shared.schemas.job_spec import JobSpec

    server.register_tool(
        "jobs.list",
        "List jobs with filtering and pagination.",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "node_id": {"type": "string"},
                "search": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "offset": {"type": "integer", "default": 0},
            },
        },
        jobs_list,
    )
    server.register_tool(
        "jobs.get",
        "Get a job by id.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs_get,
    )
    server.register_tool(
        "jobs.create",
        "Create a job from a JobSpec.",
        {
            "type": "object",
            "properties": {"spec": JobSpec.model_json_schema()},
            "required": ["spec"],
        },
        jobs_create,
        mutating=True,
    )
    server.register_tool(
        "jobs.delete",
        "Delete a job.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs_delete,
        mutating=True,
    )
    server.register_tool(
        "jobs.retry",
        "Retry a failed/cancelled job.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs_retry,
        mutating=True,
    )
    server.register_tool(
        "jobs.cancel",
        "Cancel a running/pending job.",
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "issued_by": {"type": "string"},
            },
            "required": ["job_id"],
        },
        jobs_cancel,
        mutating=True,
    )
    server.register_tool(
        "jobs.metrics",
        "Get job GPU/CPU metrics.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs_metrics,
    )
    server.register_tool(
        "jobs.logs",
        "Get job logs (HTTP fallback).",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs_logs,
    )
    server.register_tool(
        "jobs.logs.history",
        "Get job log history.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs_logs_history,
    )
    server.register_tool(
        "jobs.logs.stream",
        "Poll job log stream availability.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}, "cursor": {"type": "integer"}},
            "required": ["job_id"],
        },
        jobs_logs_stream,
    )
    server.register_tool(
        "jobs.commands.list",
        "List command/ack pairs for a job.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        jobs_commands_list,
    )
