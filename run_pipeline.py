"""命令行入口：分阶段执行评论观点语义分析流程（与 Web 服务共用 CommentAnalysisPipeline）。

用法示例：
    uv run python run_pipeline.py all
    uv run python run_pipeline.py extract --limit 50      # 小批量试跑
    uv run python run_pipeline.py cluster --force         # 调整阈值后重新聚类（不消耗 LLM）
"""
from __future__ import annotations

import argparse
import sys

from analysis import cleaning, clustering, extraction, merging, reasons, report, sampling, stats
from analysis.config import ConfigError, POST_TOPIC
from analysis.utils import LOG


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="评论观点语义分析流水线")
    parser.add_argument(
        "stage",
        choices=(
            "prepare",
            "extract",
            "repair",
            "cluster",
            "merge",
            "reasons",
            "stats",
            "report",
            "sample",
            "all",
        ),
        help="要执行的阶段；all 表示按顺序执行全部阶段",
    )
    parser.add_argument("--force", action="store_true", help="忽略已有结果，强制重新计算")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条评论（用于小批量试跑）")
    parser.add_argument("--sample-size", type=int, default=None, help="抽样校验的样本条数")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.stage in ("prepare",):
            # CLI 是针对本项目 detail_comments.json 的入口，直接使用该帖子的主题；
            # Web 任务的主题由上传文件或前端参数决定。
            cleaning.run(force=args.force, topic=POST_TOPIC)
        elif args.stage == "extract":
            extraction.run(force=args.force, limit=args.limit)
        elif args.stage == "repair":
            extraction.run(limit=args.limit, repair=True)
        elif args.stage == "cluster":
            clustering.run(force=args.force)
        elif args.stage == "merge":
            merging.run(force=args.force)
        elif args.stage == "reasons":
            reasons.run(force=args.force)
        elif args.stage == "stats":
            stats.run(force=args.force)
        elif args.stage == "report":
            report.run()
        elif args.stage == "sample":
            sampling.run(force=args.force, sample_size=args.sample_size or 100)
        else:
            from analysis.pipeline import run_all

            run_all(force=args.force, sample_size=args.sample_size)
    except ConfigError as exc:
        LOG.error("配置错误：%s", exc)
        return 2
    except KeyboardInterrupt:
        LOG.warning("已中断；已完成的进度保存在中间结果与 cache/ 中，可直接重新运行续跑")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
