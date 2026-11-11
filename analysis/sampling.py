"""阶段 9：人工抽样校验（随机抽样 + 基于人工判定的准确率计算）。"""
from __future__ import annotations

import random
from datetime import datetime

from . import config
from . import progress
from .paths import Paths, default_paths
from .utils import LOG, read_json, write_json, write_text


def build_sample(
    sample_size: int = config.SAMPLE_SIZE,
    seed: int = config.SAMPLE_RANDOM_SEED,
    *,
    paths: Paths | None = None,
) -> dict:
    paths = paths or default_paths()
    cleaned = read_json(paths.cleaned)
    extraction = read_json(paths.extraction)
    results = read_json(paths.results)
    final_viewpoints = read_json(paths.final_viewpoints)
    if not (cleaned and extraction and results):
        raise RuntimeError("请先完成分析流程，再生成抽样校验文件")

    extraction_by_id = {r["comment_id"]: r for r in extraction["results"]}
    id_to_final: dict[str, list[dict]] = {}
    for viewpoint in final_viewpoints["viewpoints"]:
        for comment_id in viewpoint["comment_ids"]:
            id_to_final.setdefault(comment_id, []).append(
                {"viewpoint_id": viewpoint["viewpoint_id"], "name": viewpoint["name"]}
            )
    comment_index = {c["comment_id"]: c for c in cleaned["comments"]}

    rng = random.Random(seed)
    ids = [c["comment_id"] for c in cleaned["comments"]]
    sample_ids = rng.sample(ids, min(sample_size, len(ids)))

    rows = []
    for comment_id in sample_ids:
        record = extraction_by_id.get(comment_id, {})
        comment = comment_index[comment_id]
        rows.append(
            {
                "comment_id": comment_id,
                "content": comment["content"],
                "like_count": comment.get("like_count", 0),
                "is_relevant": record.get("is_relevant"),
                "extracted_viewpoints": [
                    {
                        "statement": vp.get("statement"),
                        "stance": vp.get("stance"),
                        "reason": vp.get("reason"),
                    }
                    for vp in (record.get("viewpoints") or [])
                ],
                "final_viewpoints": id_to_final.get(comment_id, []),
                "manual_judgement": None,
            }
        )

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sample_size": len(rows),
            "random_seed": seed,
            "judgement_instruction": (
                "请人工逐条判断“识别出的观点/理由”是否正确："
                "在 manual_sample_check_judged.json 中填写 correct=true/false，"
                "准确率 = correct 为 true 的条数 / 已判定条数。"
            ),
        },
        "samples": rows,
    }


def compute_accuracy(judged: dict | list) -> dict:
    """根据人工判定结果计算准确率。"""
    # 形式三：{"total": 100, "incorrect": [...]} —— 只记录判定为错误的条目
    if isinstance(judged, dict) and "incorrect" in judged and "total" in judged:
        total = int(judged["total"])
        incorrect = [entry for entry in judged["incorrect"] if isinstance(entry, dict)]
        return {
            "judged_count": total,
            "correct_count": total - len(incorrect),
            "accuracy": round((total - len(incorrect)) / total * 100, 1) if total else None,
            "error_examples": [
                {
                    "comment_id": entry.get("comment_id"),
                    "content": entry.get("content", ""),
                    "note": entry.get("note", ""),
                }
                for entry in incorrect
            ],
            "borderline": judged.get("borderline", []),
            "reviewer": (judged.get("meta") or {}).get("reviewer", ""),
        }
    if isinstance(judged, dict):
        entries = judged.get("judgements") or judged.get("samples") or []
    else:
        entries = judged
    total = 0
    correct = 0
    errors = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if "correct" not in entry or entry["correct"] is None:
            continue
        total += 1
        if entry["correct"]:
            correct += 1
        else:
            errors.append(
                {
                    "comment_id": entry.get("comment_id"),
                    "content": entry.get("content", ""),
                    "note": entry.get("note", ""),
                }
            )
    return {
        "judged_count": total,
        "correct_count": correct,
        "accuracy": round(correct / total * 100, 1) if total else None,
        "error_examples": errors[:30],
    }


def render_markdown(sample: dict, accuracy: dict | None) -> str:
    lines = ["# 人工抽样校验", ""]
    lines.append(
        f"- 抽样条数：{sample['meta']['sample_size']}（随机种子 {sample['meta']['random_seed']}）"
    )
    lines.append(f"- 生成时间：{sample['meta']['generated_at']}")
    if accuracy:
        lines.append(
            f"- 人工判定条数：{accuracy['judged_count']}，判定正确：{accuracy['correct_count']}，"
            f"观点归类准确率：**{accuracy['accuracy']}%**"
        )
    else:
        lines.append("- 人工判定：待填写 `manual_sample_check_judged.json`")
    lines.append("")
    lines.append("| # | comment_id | 原始评论 | 识别观点 | 识别理由 | 归属最终观点 | 人工判定 |")
    lines.append("| ---: | --- | --- | --- | --- | --- | --- |")
    for idx, row in enumerate(sample["samples"], start=1):
        statements = "<br>".join(
            (vp.get("statement") or "") + (f"（{vp.get('stance')}）" if vp.get("stance") else "")
            for vp in row["extracted_viewpoints"]
        ) or "（无明确观点）"
        reasons = "<br>".join(vp.get("reason") or "—" for vp in row["extracted_viewpoints"]) or "—"
        finals = "<br>".join(f"{f['viewpoint_id']} {f['name']}" for f in row["final_viewpoints"]) or "—"
        content = row["content"].replace("|", "｜").replace("\n", " ")
        judgement = "正确" if row.get("manual_judgement") is True else ("错误" if row.get("manual_judgement") is False else "")
        lines.append(
            f"| {idx} | {row['comment_id']} | {content} | {statements} | {reasons} | {finals} | {judgement} |"
        )
    lines.append("")
    if accuracy and accuracy["error_examples"]:
        lines.append("## 判定为错误的样例")
        lines.append("")
        for error in accuracy["error_examples"]:
            lines.append(f"- `{error['comment_id']}` {error['content']} —— {error['note']}")
        lines.append("")
    return "\n".join(lines)


def run(*, force: bool = False, sample_size: int = config.SAMPLE_SIZE, paths: Paths | None = None) -> dict:
    paths = paths or default_paths()
    progress.report("sampling", "正在生成人工抽样校验数据…")
    sample = build_sample(sample_size=sample_size, paths=paths)

    judged = read_json(paths.sample_judged)
    accuracy = None
    if judged:
        accuracy = compute_accuracy(judged)
        # 把人工判定回填到样本里，方便查看
        judged_entries = judged.get("judgements") if isinstance(judged, dict) else judged
        judgement_map = {
            entry["comment_id"]: entry
            for entry in (judged_entries or [])
            if isinstance(entry, dict) and entry.get("comment_id")
        }
        for row in sample["samples"]:
            entry = judgement_map.get(row["comment_id"])
            if entry:
                row["manual_judgement"] = entry.get("correct")
                row["manual_note"] = entry.get("note", "")
        sample["meta"]["accuracy"] = accuracy
        LOG.info(
            "人工判定准确率：%s%%（%d/%d）",
            accuracy["accuracy"],
            accuracy["correct_count"],
            accuracy["judged_count"],
        )

    write_json(paths.sample, sample)
    write_text(paths.sample_md, render_markdown(sample, accuracy))
    LOG.info("抽样校验文件已生成：%s / %s", paths.sample.name, paths.sample_md.name)
    progress.report(
        "sampling",
        f"抽样校验完成{'：准确率 ' + str(accuracy['accuracy']) + '%' if accuracy else ''}",
        ratio=1.0,
    )
    return sample
