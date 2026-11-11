"""阶段 7：Python 侧统计（人数、占比、代表性评论、追溯校验）。"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime

from . import config
from . import progress
from .paths import Paths, default_paths
from .utils import LOG, read_json, write_json


def _round1(value: float) -> float:
    return round(float(value), 1)


def compute_results(
    cleaned: dict,
    extraction: dict,
    final_viewpoints: dict,
    *,
    representative_limit: int = config.REPRESENTATIVE_COMMENT_LIMIT,
) -> dict:
    comments = cleaned["comments"]
    comment_index = {c["comment_id"]: c for c in comments}
    valid_comment_count = len(comments)

    extraction_by_id = {r["comment_id"]: r for r in extraction["results"]}
    irrelevant = [cid for cid, r in extraction_by_id.items() if r.get("is_relevant") is False]
    relevant = [cid for cid, r in extraction_by_id.items() if r.get("is_relevant") is True]
    unknown = [cid for cid, r in extraction_by_id.items() if r.get("is_relevant") is None]
    unclear = [cid for cid in relevant if not (extraction_by_id[cid].get("viewpoints") or [])]

    viewpoints_payload = []
    missing_ids: list[str] = []
    for viewpoint in final_viewpoints["viewpoints"]:
        comment_ids = [cid for cid in viewpoint["comment_ids"] if cid in comment_index]
        missing_ids.extend([cid for cid in viewpoint["comment_ids"] if cid not in comment_index])
        comment_count = len(comment_ids)

        # 代表性评论：优先点赞高、其次信息量大（长度），且内容互不重复
        candidates = sorted(
            (comment_index[cid] for cid in comment_ids),
            key=lambda c: (-c.get("like_count", 0), -len(c["content"])),
        )
        representative = []
        seen_contents = set()
        for candidate in candidates:
            if candidate["content"] in seen_contents:
                continue
            seen_contents.add(candidate["content"])
            representative.append(candidate)
            if len(representative) >= representative_limit:
                break

        reasons_payload = []
        for reason in viewpoint.get("reasons", []):
            reason_comment_ids = [cid for cid in reason["comment_ids"] if cid in comment_index]
            reasons_payload.append(
                {
                    "reason_id": reason["reason_id"],
                    "name": reason["name"],
                    "description": reason.get("description", ""),
                    "comment_count": len(reason_comment_ids),
                    "percentage": _round1(len(reason_comment_ids) / comment_count * 100) if comment_count else 0.0,
                    "comment_ids": reason_comment_ids,
                }
            )
        reasons_payload.sort(key=lambda r: (-r["comment_count"], r["name"]))

        viewpoints_payload.append(
            {
                "viewpoint_id": viewpoint["viewpoint_id"],
                "name": viewpoint["name"],
                "description": viewpoint.get("description", ""),
                "stance": viewpoint.get("stance", "unclear"),
                "stance_distribution": viewpoint.get("stance_distribution", {}),
                "comment_count": comment_count,
                "percentage": _round1(comment_count / valid_comment_count * 100) if valid_comment_count else 0.0,
                "reason_count": len(reasons_payload),
                "reasons": reasons_payload,
                "representative_comments": [c["content"] for c in representative],
                "representative_comment_details": [
                    {
                        "comment_id": c["comment_id"],
                        "content": c["content"],
                        "like_count": c.get("like_count", 0),
                    }
                    for c in representative
                ],
                "comment_ids": comment_ids,
                "statements": viewpoint.get("statements", []),
                "cluster_ids": viewpoint.get("cluster_ids", []),
                "reason_stats": viewpoint.get("reason_stats", {}),
                "merged_from": viewpoint.get("merged_from", []),
            }
        )

    viewpoints_payload.sort(key=lambda v: (-v["comment_count"], v["name"]))
    total_links = sum(v["comment_count"] for v in viewpoints_payload)
    comments_with_viewpoint = {
        cid for viewpoint in viewpoints_payload for cid in viewpoint["comment_ids"]
    }

    summary = {
        "raw_comment_count": cleaned["meta"]["raw_comment_count"],
        "valid_comment_count": valid_comment_count,
        "invalid_comment_count": cleaned["meta"]["invalid_comment_count"],
        "invalid_reasons": cleaned["meta"]["invalid_reasons"],
        "duplicate_comment_count": cleaned["meta"]["duplicate_comment_count"],
        "irrelevant_comment_count": len(irrelevant),
        "relevant_comment_count": len(relevant),
        "unclear_comment_count": len(unclear),
        "unknown_comment_count": len(unknown),
        "comment_with_viewpoint_count": len(comments_with_viewpoint),
        "viewpoint_count": len(viewpoints_payload),
        "viewpoint_comment_link_count": total_links,
        "avg_viewpoints_per_valid_comment": _round1(total_links / valid_comment_count) if valid_comment_count else 0.0,
        "multi_viewpoint_comment_count": sum(
            1 for cid in relevant if len(extraction_by_id[cid].get("viewpoints") or []) > 1
        ),
    }

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_file": cleaned["meta"]["source_file"],
            "post_topic": cleaned["meta"]["post_topic"],
            "chat_model": config.CHAT_MODEL,
            "embedding_model": config.EMBEDDING_MODEL_REPO,
            "statistics_note": (
                "所有人数与占比均由程序按 comment_id 去重统计；LLM 仅用于语义理解、"
                "观点归并与理由归纳，不参与计数。"
            ),
            "percentage_base": {
                "viewpoint": "有效评论数（valid_comment_count）",
                "reason": "该观点的评论数（viewpoint.comment_count）",
            },
        },
        "summary": summary,
        "viewpoints": viewpoints_payload,
        "unclear_comment_examples": [
            {"comment_id": cid, "content": comment_index[cid]["content"]}
            for cid in unclear[:15]
            if cid in comment_index
        ],
        "irrelevant_comment_examples": [
            {"comment_id": cid, "content": comment_index[cid]["content"]}
            for cid in irrelevant[:15]
            if cid in comment_index
        ],
        "integrity": {
            "comment_ids_missing_from_cleaned": sorted(set(missing_ids)),
            "all_viewpoint_comment_ids_traceable": not missing_ids,
            "representative_comments_from_raw_data": True,
        },
    }


def run(force: bool = False, *, paths: Paths | None = None) -> dict:
    paths = paths or default_paths()
    if paths.results.exists() and not force:
        LOG.info("统计结果已存在，直接复用：%s", paths.results)
        progress.report("statistics", "复用已有统计结果", ratio=1.0)
        return read_json(paths.results)

    started = time.time()
    cleaned = read_json(paths.cleaned)
    extraction = read_json(paths.extraction)
    final_viewpoints = read_json(paths.final_viewpoints)
    for name, payload in (
        (paths.cleaned.name, cleaned),
        (paths.extraction.name, extraction),
        (paths.final_viewpoints.name, final_viewpoints),
    ):
        if not payload:
            raise RuntimeError(f"缺少中间结果：{name}")

    results = compute_results(cleaned, extraction, final_viewpoints)
    write_json(paths.results, results)
    summary = results["summary"]
    LOG.info(
        "统计完成：有效评论 %d，观点 %d，观点-评论关联 %d，耗时 %.1fs",
        summary["valid_comment_count"],
        summary["viewpoint_count"],
        summary["viewpoint_comment_link_count"],
        time.time() - started,
    )
    progress.report(
        "statistics",
        f"统计完成：{summary['viewpoint_count']} 个观点，共 {summary['viewpoint_comment_link_count']} 条观点-评论关联",
        ratio=1.0,
    )
    return results
