"""阶段 8：生成 Markdown 分析报告。"""
from __future__ import annotations

from datetime import datetime

from . import config
from . import progress
from .paths import Paths, default_paths
from .utils import LOG, read_json, write_text
from .report_html import build_html_report

STANCE_LABELS = {
    "support": "支持",
    "oppose": "反对",
    "conditional": "有条件",
    "neutral": "中立",
    "unclear": "不明",
}

# 评论数达到该阈值的观点视为“主要观点”，其余在报告中作为长尾观点汇总展示
MAIN_VIEWPOINT_MIN = 3
# 每个观点在报告中最多展开的理由行数
MAX_REASONS_SHOWN = 8


def _summary_table(results: dict) -> str:
    lines = ["| 排名 | 观点 | 评论数 | 占有效评论比例 |", "| ---: | --- | ---: | ---: |"]
    for rank, viewpoint in enumerate(results["viewpoints"], start=1):
        lines.append(
            f"| {rank} | **{viewpoint['name']}** | {viewpoint['comment_count']} | {viewpoint['percentage']}% |"
        )
    lines.append(
        f"| 合计 | 观点-评论关联（含一条评论多个观点） | {results['summary']['viewpoint_comment_link_count']} | "
        f"{round(results['summary']['viewpoint_comment_link_count'] / max(results['summary']['valid_comment_count'], 1) * 100, 1)}% |"
    )
    return "\n".join(lines)


def build_report(results: dict, *, sample_file: Path | None = None) -> str:
    summary = results["summary"]
    meta = results["meta"]
    lines: list[str] = []
    lines.append("# 评论观点分析报告")
    lines.append("")
    topic_text = meta.get("post_topic") or "未提供主题（由评论内容自行判断讨论话题）"
    lines.append(f"- 讨论主题：{topic_text}")
    lines.append(f"- 数据来源：`{meta['source_file']}`")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 语义分析模型：`{meta['chat_model']}`；向量模型：`{meta['embedding_model']}`")
    lines.append("")

    lines.append("## 一、数据概况")
    lines.append("")
    lines.append("| 指标 | 数量 |")
    lines.append("| --- | ---: |")
    lines.append(f"| 原始评论数量 | {summary['raw_comment_count']} |")
    lines.append(f"| 有效评论数量（进入语义分析） | {summary['valid_comment_count']} |")
    lines.append(f"| 无效评论数量（空/纯标点表情/广告） | {summary['invalid_comment_count']} |")
    lines.append(f"| 重复评论数量 | {summary['duplicate_comment_count']} |")
    lines.append(f"| 与主题无关评论数量 | {summary['irrelevant_comment_count']} |")
    lines.append(f"| 相关但无法识别明确观点数量 | {summary['unclear_comment_count']} |")
    lines.append(f"| 表达出至少一个观点的评论数量 | {summary['comment_with_viewpoint_count']} |")
    lines.append(f"| 包含多个观点的评论数量 | {summary['multi_viewpoint_comment_count']} |")
    lines.append(f"| 最终观点数量 | {summary['viewpoint_count']} |")
    lines.append(
        f"| 观点-评论关联总数（去重后合计） | {summary['viewpoint_comment_link_count']} |"
    )
    lines.append(
        f"| 平均每条有效评论表达观点数 | {summary['avg_viewpoints_per_valid_comment']} |"
    )
    lines.append("")
    lines.append(
        "> 统计口径：由于一条评论可能同时表达多个观点，“所有观点评论数之和”可能大于有效评论数，"
        "这是正常情况。观点占比 = 该观点评论数 ÷ 有效评论数；理由占比 = 该理由评论数 ÷ 该观点评论数。"
    )
    lines.append("")

    lines.append("## 二、核心观点统计")
    lines.append("")
    lines.append(_summary_table(results))
    lines.append("")
    lines.append(
        "> 表中的“排名”仅表示评论数量从高到低的展示顺序，不代表对观点价值的评价。"
    )
    lines.append("")

    lines.append("## 三、观点详细分析")
    lines.append("")
    main_viewpoints = [v for v in results["viewpoints"] if v["comment_count"] >= MAIN_VIEWPOINT_MIN]
    tail_viewpoints = [v for v in results["viewpoints"] if v["comment_count"] < MAIN_VIEWPOINT_MIN]
    if tail_viewpoints:
        lines.append(
            f"下面先详细展开评论数 ≥ {MAIN_VIEWPOINT_MIN} 的 {len(main_viewpoints)} 个主要观点，"
            f"评论数更少的 {len(tail_viewpoints)} 个长尾观点汇总在文末，避免长尾噪声干扰主要结论。"
        )
        lines.append("")
    for viewpoint in main_viewpoints:
        lines.append(f"### {viewpoint['viewpoint_id']} {viewpoint['name']}")
        lines.append("")
        lines.append(f"**评论数量：** {viewpoint['comment_count']} 条（占有效评论 {viewpoint['percentage']}%）")
        lines.append("")
        stance = STANCE_LABELS.get(viewpoint.get("stance", "unclear"), viewpoint.get("stance", "unclear"))
        distribution = viewpoint.get("stance_distribution") or {}
        distribution_text = "、".join(
            f"{STANCE_LABELS.get(k, k)} {v}" for k, v in sorted(distribution.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"**立场倾向：** {stance}" + (f"（成员立场分布：{distribution_text}）" if distribution_text else ""))
        lines.append("")
        lines.append(f"**观点说明：** {viewpoint['description'] or '—'}")
        lines.append("")
        if viewpoint["reasons"]:
            lines.append("**主要理由：**")
            lines.append("")
            shown = [r for r in viewpoint["reasons"] if r["comment_count"] >= 2][:MAX_REASONS_SHOWN]
            if not shown:
                shown = viewpoint["reasons"][:3]
            lines.append("| 理由 | 评论数 | 占该观点比例 |")
            lines.append("| --- | ---: | ---: |")
            for reason in shown:
                lines.append(f"| {reason['name']} | {reason['comment_count']} | {reason['percentage']}% |")
            lines.append("")
            hidden = len(viewpoint["reasons"]) - len(shown)
            if hidden > 0:
                lines.append(
                    f"> 另有 {hidden} 个提及较少的次要理由未在表中展开（完整数据见 `analysis_results.json`）。"
                )
                lines.append("")
            no_reason = viewpoint.get("reason_stats", {}).get("comment_without_reason_count", 0)
            if no_reason:
                lines.append(f"> 另有 {no_reason} 条评论表达该观点但没有给出明确理由。")
                lines.append("")
        else:
            lines.append("**主要理由：** 评论中未给出明确理由。")
            lines.append("")
        lines.append("**代表性评论：**")
        lines.append("")
        for comment in viewpoint["representative_comments"]:
            lines.append(f"- {comment}")
        lines.append("")

    if tail_viewpoints:
        lines.append(f"### 附：长尾观点（评论数 1 ~ {MAIN_VIEWPOINT_MIN - 1}，{len(tail_viewpoints)} 个）")
        lines.append("")
        lines.append("| 观点 | 评论数 | 占有效评论比例 | 代表性评论 |")
        lines.append("| --- | ---: | ---: | --- |")
        for viewpoint in tail_viewpoints:
            sample = (viewpoint["representative_comments"] or [""])[0].replace("|", "｜").replace("\n", " ")
            lines.append(
                f"| {viewpoint['name']} | {viewpoint['comment_count']} | {viewpoint['percentage']}% | {sample[:40]} |"
            )
        lines.append("")

    lines.append("## 四、无法明确判断的评论")
    lines.append("")
    lines.append(
        f"- 相关但没有明确观点：{summary['unclear_comment_count']} 条"
    )
    lines.append(f"- 与主题无关：{summary['irrelevant_comment_count']} 条")
    if results["unclear_comment_examples"]:
        lines.append("")
        lines.append("没有明确观点的评论示例：")
        lines.append("")
        for example in results["unclear_comment_examples"][:10]:
            lines.append(f"- {example['content']}")
    if results["irrelevant_comment_examples"]:
        lines.append("")
        lines.append("与主题无关的评论示例：")
        lines.append("")
        for example in results["irrelevant_comment_examples"][:10]:
            lines.append(f"- {example['content']}")
    lines.append("")

    lines.append("## 五、分析方法说明")
    lines.append("")
    lines.append("```text")
    lines.append("detail_comments.json")
    lines.append("        ↓ 数据读取 + 清洗去重（Python）")
    lines.append("cleaned_comments.json")
    lines.append("        ↓ LLM 批量语义提取（每条评论 → 0..n 个观点，含 stance / reason）")
    lines.append("llm_comment_analysis.json")
    lines.append("        ↓ Embedding 生成语义向量（本地 bge-base-zh-v1.5）")
    lines.append("viewpoint_embeddings.npz")
    lines.append("        ↓ 余弦相似度聚类")
    lines.append("viewpoint_clusters.json")
    lines.append("        ↓ LLM 归并 / 命名最终观点（允许合并与拆分）")
    lines.append("final_viewpoints.json")
    lines.append("        ↓ 程序绑定 viewpoint_id 并统计人数、占比（Python）")
    lines.append("        ↓ LLM 对每个观点的理由归纳 + 程序统计理由人数")
    lines.append("analysis_results.json → analysis_report.md")
    lines.append("```")
    lines.append("")
    lines.append(
        "> 本报告中的评论数量由程序根据评论 ID 进行统计，LLM 仅用于语义理解、观点归纳和理由总结。"
    )
    lines.append("")
    lines.append("### 可追溯性与复现")
    lines.append("")
    lines.append(
        "- 每个观点均保留 `comment_ids`（原始评论 ID）与 `representative_comments`（原文），可逐条回溯到 `detail_comments.json`。"
    )
    lines.append(
        "- LLM 与 Embedding 调用均写入 `cache/` 缓存；重跑流程不会重复消耗 API，并支持中断后继续。"
    )
    lines.append(
        "- 阶段化命令：`uv run python run_pipeline.py {prepare|extract|cluster|merge|reasons|stats|report|sample|all}`。"
    )
    lines.append("")

    sample = read_json(sample_file or config.SAMPLE_FILE) or {}
    accuracy = (sample.get("meta") or {}).get("accuracy")
    if accuracy:
        lines.append("## 六、人工抽样校验")
        lines.append("")
        lines.append(
            f"- 抽样方式：从 {summary['valid_comment_count']} 条有效评论中随机抽取 "
            f"{sample['meta']['sample_size']} 条（随机种子 {sample['meta']['random_seed']}）"
        )
        lines.append(
            f"- 人工判定：{accuracy['judged_count']} 条，其中判定正确 {accuracy['correct_count']} 条，"
            f"**观点归类准确率 {accuracy['accuracy']}%**"
        )
        lines.append(f"- 判定人：{accuracy.get('reviewer') or '人工复核'}")
        lines.append("")
        if accuracy.get("error_examples"):
            lines.append("判定有误的样例：")
            lines.append("")
            for error in accuracy["error_examples"]:
                lines.append(f"- `{error['comment_id']}` {error['content'][:60]} —— {error['note']}")
            lines.append("")
        lines.append(
            "逐条明细见 `manual_sample_check.md`（含原始评论、识别观点、识别理由、归属观点与人工判定），"
            "判定结果见 `manual_sample_check_judged.json`。"
        )
        lines.append("")

    return "\n".join(lines)


def run(force: bool = False, *, paths: Paths | None = None) -> str:
    paths = paths or default_paths()
    results = read_json(paths.results)
    if not results:
        raise RuntimeError("请先运行统计（analysis_results.json 不存在）")
    progress.report("report", "正在生成 Markdown 报告…")
    markdown = build_report(results, sample_file=paths.sample)
    write_text(paths.report, markdown)

    # 同时生成单文件 HTML 报告（内含完整数据，可离线打开 / 直接分发）
    progress.report("report", "正在生成 HTML 报告…")
    cleaned = read_json(paths.cleaned) or {}
    comments = {
        comment["comment_id"]: {
            "comment_id": comment["comment_id"],
            "content": comment["content"],
            "like_count": comment.get("like_count", 0),
        }
        for comment in cleaned.get("comments", [])
    }
    html_report = build_html_report(results, comments=comments)
    write_text(paths.report_html, html_report)

    LOG.info("分析报告已生成：%s", paths.report)
    LOG.info("HTML 报告已生成：%s（%.1f KB）", paths.report_html, len(html_report.encode("utf-8")) / 1024)
    progress.report("report", "报告已生成", ratio=1.0)
    return markdown
