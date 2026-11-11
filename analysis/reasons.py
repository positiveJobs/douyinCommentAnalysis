"""阶段 6：每个最终观点内部的理由归纳（LLM 语义提取 + Embedding 聚类 + LLM 归并 + Python 统计）。"""
from __future__ import annotations

import time
from collections import Counter, OrderedDict
from datetime import datetime

import numpy as np

from . import config
from . import cancel
from . import progress
from .clustering import cluster_vectors
from .embedding import LocalEmbedder
from .llm import LLMClient
from .merging import consolidate_reasons, dedupe_by_embedding, merge_groups
from .paths import Paths, default_paths
from .utils import LOG, read_json, write_json


def collect_reasons(viewpoint: dict, statement_map: dict[str, list[tuple[str, str]]]) -> OrderedDict[str, list[str]]:
    """收集该观点下各评论给出的理由（同一理由合并评论）。"""
    statement_texts = {item["text"] for item in viewpoint.get("statements", [])}
    reasons: OrderedDict[str, list[str]] = OrderedDict()
    for statement in statement_texts:
        for comment_id, reason in statement_map.get(statement, []):
            if not reason:
                continue
            reasons.setdefault(reason, []).append(comment_id)
    return reasons


def build_statement_map(extraction: dict) -> dict[str, list[tuple[str, str]]]:
    """statement 文本 -> [(comment_id, reason), ...]"""
    mapping: dict[str, list[tuple[str, str]]] = {}
    for record in extraction["results"]:
        if record.get("is_relevant") is not True:
            continue
        for viewpoint in record.get("viewpoints") or []:
            statement = (viewpoint.get("statement") or "").strip()
            if not statement:
                continue
            mapping.setdefault(statement, []).append(
                (record["comment_id"], (viewpoint.get("reason") or "").strip())
            )
    return mapping


def merge_reasons_for_viewpoint(
    client: LLMClient,
    embedder: LocalEmbedder,
    viewpoint: dict,
    reasons: "OrderedDict[str, list[str]]",
    vectors_by_reason: dict[str, np.ndarray],
) -> list[dict]:
    """对一个观点的理由做聚类 + LLM 归并，返回 reason 列表（不含最终 id）。"""
    if not reasons:
        return []
    texts = list(reasons)
    vectors = np.vstack([vectors_by_reason[t] for t in texts])
    labels = cluster_vectors(
        vectors, threshold=config.REASON_CLUSTER_THRESHOLD, stage="reason_analysis"
    )

    groups: dict[int, list[str]] = {}
    for text, label in zip(texts, labels):
        groups.setdefault(int(label), []).append(text)

    group_payload = []
    index_to_text: dict[str, str] = {}
    for order, members in enumerate(
        sorted(groups.values(), key=lambda m: (-len(m), texts.index(m[0]))), start=1
    ):
        items = []
        for text in members:
            item_id = f"T{order:03d}-{len(items) + 1}"
            index_to_text[item_id] = text
            items.append({"item_id": item_id, "text": text})
        group_payload.append({"group_id": f"G{order:03d}", "items": items})

    merged = merge_groups(
        client,
        group_payload,
        include_stance=False,
        kind=f"reason-{viewpoint['viewpoint_id']}",
        embedder=embedder,
        stage="reason_analysis",
    )

    # 跨 chunk 去重：同一理由的不同措辞可能落在不同 chunk 中，需要再做一次语义归并
    dedupe_items = []
    for entry in merged:
        comment_ids: set[str] = set()
        source_texts = []
        for item_id in entry["item_ids"]:
            text = index_to_text.get(item_id)
            if text is None:
                continue
            comment_ids.update(reasons[text])
            source_texts.append(text)
        if not comment_ids:
            continue
        dedupe_items.append(
            {
                "name": entry["name"],
                "description": entry.get("description", ""),
                "comment_ids": sorted(comment_ids),
                "source_reasons": source_texts,
            }
        )
    if len(dedupe_items) > 1:
        dedupe_items = dedupe_by_embedding(
            client,
            embedder,
            dedupe_items,
            threshold=config.REASON_CLUSTER_THRESHOLD,
            include_stance=False,
            kind=f"reason-{viewpoint['viewpoint_id']}",
        )
        dedupe_items = consolidate_reasons(client, embedder, dedupe_items)

    results = []
    for entry in dedupe_items:
        members = entry.get("members") or [entry]
        comment_ids: set[str] = set()
        source_texts: list[str] = []
        for member in members:
            comment_ids.update(member.get("comment_ids", []))
            source_texts.extend(member.get("source_reasons", []))
        if not comment_ids:
            continue
        results.append(
            {
                "name": entry["name"],
                "description": entry.get("description", ""),
                "comment_ids": sorted(comment_ids),
                "source_reasons": source_texts,
            }
        )
    results.sort(key=lambda r: (-len(r["comment_ids"]), r["name"]))
    return results


def run(*, force: bool = False, paths: Paths | None = None) -> dict:
    """为 final_viewpoints.json 中每个观点补充 reasons。"""
    config.require_llm_config()
    paths = paths or default_paths()
    document = read_json(paths.final_viewpoints)
    if not document:
        raise RuntimeError("请先运行观点归并（final_viewpoints.json 不存在）")
    if document.get("viewpoints") and all("reasons" in v for v in document["viewpoints"]) and not force:
        LOG.info("理由归纳已存在，直接复用：%s", paths.final_viewpoints)
        progress.report("reason_analysis", "复用已有理由归纳结果", ratio=1.0)
        return document

    started = time.time()
    extraction = read_json(paths.extraction)
    statement_map = build_statement_map(extraction)
    viewpoints = document["viewpoints"]

    per_viewpoint_reasons = [collect_reasons(v, statement_map) for v in viewpoints]
    all_reasons = []
    for reasons in per_viewpoint_reasons:
        all_reasons.extend(reasons)
    unique_reasons = list(dict.fromkeys(all_reasons))
    progress.report("reason_analysis", f"正在归纳 {len(viewpoints)} 个观点下的理由…")
    embedder = LocalEmbedder()
    reason_vectors = embedder.encode(unique_reasons, progress_label="理由 ", stage="reason_analysis")
    vectors_by_reason = {text: reason_vectors[i] for i, text in enumerate(unique_reasons)}

    with LLMClient() as client:
        merged_results = client.map_concurrent(
            lambda payload: merge_reasons_for_viewpoint(
                client, embedder, payload[0], payload[1], vectors_by_reason
            ),
            list(zip(viewpoints, per_viewpoint_reasons)),
            label="理由归并",
        )
        cancel.check("理由归纳已取消")
        llm_stats = dict(client.stats)

    for viewpoint, reasons, merged in zip(viewpoints, per_viewpoint_reasons, merged_results):
        if isinstance(merged, Exception):
            LOG.warning("观点 %s 理由归并失败：%s", viewpoint["viewpoint_id"], merged)
            merged = []
        reason_entries = []
        for order, entry in enumerate(merged, start=1):
            reason_entries.append(
                {
                    "reason_id": f"{viewpoint['viewpoint_id']}-R{order:02d}",
                    "name": entry["name"],
                    "description": entry.get("description", ""),
                    "comment_ids": entry["comment_ids"],
                    "comment_count": len(entry["comment_ids"]),
                }
            )
        covered = {cid for entry in reason_entries for cid in entry["comment_ids"]}
        viewpoint["reasons"] = reason_entries
        viewpoint["reason_stats"] = {
            "reason_count": len(reason_entries),
            "unique_reason_text_count": len(reasons),
            "comment_without_reason_count": len(set(viewpoint["comment_ids"]) - covered),
        }

    document["meta"]["reason_stage"] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cluster_threshold": config.REASON_CLUSTER_THRESHOLD,
        "unique_reason_text_count": len(unique_reasons),
        "llm_stats": llm_stats,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    write_json(paths.final_viewpoints, document)
    LOG.info(
        "理由归纳完成：%d 个观点，%d 条唯一理由表述，耗时 %.1fs",
        len(viewpoints),
        len(unique_reasons),
        time.time() - started,
    )
    progress.report("reason_analysis", f"理由归纳完成：覆盖 {len(viewpoints)} 个观点", ratio=1.0)
    return document
