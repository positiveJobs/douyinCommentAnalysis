"""查看分析结果的小工具：观点分布、单条评论详情。

用法：
    uv run python tools/inspect_results.py viewpoints --top 20
    uv run python tools/inspect_results.py viewpoints --task 20260919_105816_723523
    uv run python tools/inspect_results.py comment c001
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.paths import default_paths, task_paths  # noqa: E402
from analysis.utils import read_json  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def resolve_paths(task_id: str | None):
    return task_paths(task_id) if task_id else default_paths()


def cmd_viewpoints(args) -> None:
    paths = resolve_paths(args.task)
    data = read_json(paths.results)
    if not data:
        print(f"找不到结果：{paths.results}")
        return
    summary = data["summary"]
    print("== 数据概况 ==")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    viewpoints = data["viewpoints"]
    buckets = Counter()
    for viewpoint in viewpoints:
        count = viewpoint["comment_count"]
        label = (
            ">=20"
            if count >= 20
            else "10-19"
            if count >= 10
            else "5-9"
            if count >= 5
            else "2-4"
            if count >= 2
            else "1"
        )
        buckets[label] += 1
    print(f"\n== 观点 {len(viewpoints)} 个，按提及数分布：{dict(buckets)} ==")
    for idx, viewpoint in enumerate(viewpoints[: args.top], start=1):
        print(
            f"{idx:3d}. [{viewpoint['comment_count']:3d} 条 {viewpoint['percentage']}%] "
            f"{viewpoint['viewpoint_id']} {viewpoint['name']} —— {viewpoint.get('description', '')}"
        )


def cmd_comment(args) -> None:
    paths = resolve_paths(args.task)
    extraction = read_json(paths.extraction)
    cleaned = read_json(paths.cleaned)
    results = read_json(paths.results)
    content = {c["comment_id"]: c["content"] for c in cleaned["comments"]}
    record = next((r for r in extraction["results"] if r["comment_id"] == args.comment_id), None)
    if record is None:
        print("未找到该评论")
        return
    print("原文:", content.get(args.comment_id, ""))
    print("is_relevant:", record.get("is_relevant"))
    for viewpoint in record.get("viewpoints") or []:
        print(f"  观点: {viewpoint['statement']}  立场: {viewpoint['stance']}  理由: {viewpoint['reason']}")
    for viewpoint in results["viewpoints"]:
        if args.comment_id in viewpoint["comment_ids"]:
            print(f"  → 归属最终观点: {viewpoint['viewpoint_id']} {viewpoint['name']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="分析结果查看工具")
    sub = parser.add_subparsers(dest="command", required=True)
    p1 = sub.add_parser("viewpoints", help="查看观点统计")
    p1.add_argument("--top", type=int, default=40)
    p1.add_argument("--task", default=None, help="Web 任务 ID（默认查看 CLI 结果）")
    p1.set_defaults(func=cmd_viewpoints)
    p2 = sub.add_parser("comment", help="查看单条评论的分析结果")
    p2.add_argument("comment_id")
    p2.add_argument("--task", default=None, help="Web 任务 ID（默认查看 CLI 结果）")
    p2.set_defaults(func=cmd_comment)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
