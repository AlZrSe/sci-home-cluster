"""MCP prompts for Scientific Home Cluster."""

from typing import Any, Dict, List


async def run_experiment(ctx, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    name = args.get("name", "experiment")
    command = args.get("command", "")
    working_dir = args.get("working_dir", "")
    resources = args.get("resources")
    content = (
        f"Create a JobSpec with name={name!r}, command={command!r}, "
        f"working_dir={working_dir!r} and call jobs.create. "
        f"Resources: {resources!r}."
    )
    return [{"role": "user", "content": {"type": "text", "text": content}}]


async def analyze_results(ctx, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    job_id = args.get("job_id", "")
    content = (
        f"Fetch job state with jobs.get({job_id}), then jobs.logs and jobs.metrics. "
        "Summarize status, exit code, key metrics, and artifact paths."
    )
    return [{"role": "user", "content": {"type": "text", "text": content}}]


async def compare_metrics(ctx, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    job_ids = args.get("job_ids", [])
    content = (
        f"Fetch metrics for jobs {job_ids} via jobs.metrics and compare "
        "utilization, memory, and temperature across them."
    )
    return [{"role": "user", "content": {"type": "text", "text": content}}]


def register(server):
    server.register_prompt(
        "run_experiment",
        "Guide creation of a JobSpec and launch via jobs.create.",
        [
            {"name": "name", "description": "Job name", "required": False},
            {"name": "command", "description": "Command to run", "required": False},
            {
                "name": "working_dir",
                "description": "Working directory",
                "required": False,
            },
            {
                "name": "resources",
                "description": "Resource requirements",
                "required": False,
            },
        ],
        run_experiment,
    )
    server.register_prompt(
        "analyze_results",
        "Guide analysis of a job's results.",
        [{"name": "job_id", "description": "Job id", "required": True}],
        analyze_results,
    )
    server.register_prompt(
        "compare_metrics",
        "Guide comparison of metrics across jobs.",
        [{"name": "job_ids", "description": "List of job ids", "required": True}],
        compare_metrics,
    )
