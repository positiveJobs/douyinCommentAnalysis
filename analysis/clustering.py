"""阶段 4：观点 statement 的向量化与候选聚类。"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import config
from . import cancel
from . import progress
from .embedding import LocalEmbedder
from .paths import Paths, default_paths
from .utils import LOG, read_json, write_json


def load_viewpoint_items(extraction: dict | None = None, *, paths: Paths | None = None) -> list[dict]:
    """把所有评论中提取出的观点展开成条目列表。"""
    extraction = extraction or read_json((paths or default_paths()).extraction)
    if not extraction:
        raise RuntimeError("请先运行语义提取（llm_comment_analysis.json 不存在）")
    items: list[dict] = []
    for record in extraction["results"]:
        if record.get("is_relevant") is not True:
            continue
        for index, viewpoint in enumerate(record.get("viewpoints") or []):
            statement = (viewpoint.get("statement") or "").strip()
            if not statement:
                continue
            items.append(
                {
                    "comment_id": record["comment_id"],
                    "index": index,
                    "statement": statement,
                    "stance": viewpoint.get("stance", "unclear"),
                    "reason": (viewpoint.get("reason") or "").strip(),
                }
            )
    return items


def cluster_vectors(
    vectors: np.ndarray,
    *,
    threshold: float,
    progress_label: str = "",
    stage: str = "clustering",
) -> np.ndarray:
    """余弦相似度聚类，返回标签数组。

    - 数据量较小时用层次聚类（average linkage），质量更好；
    - 数据量很大时（例如上万条观点）层次聚类需要 O(n²) 内存，改用分块相似度 +
      并查集（单链聚类），内存占用从 O(n²) 降到 O(n)，避免爆内存。
    """
    if len(vectors) == 0:
        return np.zeros(0, dtype=int)
    if len(vectors) == 1:
        return np.zeros(1, dtype=int)
    cancel.check("聚类已取消")
    if len(vectors) > config.CLUSTER_AGGLOMERATIVE_MAX:
        LOG.info(
            "观点数 %d 超过层次聚类上限 %d，改用分块相似度聚类（内存友好）",
            len(vectors),
            config.CLUSTER_AGGLOMERATIVE_MAX,
        )
        return _cluster_chunked(vectors, threshold=threshold, label=progress_label, stage=stage)
    from sklearn.cluster import AgglomerativeClustering

    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=1.0 - threshold,
        metric="cosine",
        linkage="average",
    )
    return model.fit_predict(vectors)


def _cluster_chunked(
    vectors: np.ndarray,
    *,
    threshold: float,
    block_size: int = 1024,
    label: str = "",
    stage: str = "clustering",
) -> np.ndarray:
    """分块计算余弦相似度 + 并查集聚类（单链），内存占用与 n 成正比。"""
    total = len(vectors)
    parent = list(range(total))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    started = time.monotonic()
    for start in range(0, total, block_size):
        cancel.check("聚类已取消")
        stop = min(start + block_size, total)
        similarity = vectors[start:stop] @ vectors.T
        for offset in range(stop - start):
            row = similarity[offset]
            row[: start + offset + 1] = -1.0  # 只处理右上三角，避免重复
            for neighbor in np.nonzero(row >= threshold)[0]:
                union(start + offset, int(neighbor))
        progress.report(
            stage,
            progress.progress_text(stop, total, started, unit="条", prefix=f"{label}相似度计算 "),
            current=stop,
            total=total,
        )
    roots = np.array([find(i) for i in range(total)], dtype=int)
    _, labels = np.unique(roots, return_inverse=True)
    return labels.astype(int)


def build_clusters(items: list[dict], embedder: LocalEmbedder, *, threshold: float) -> dict:
    """按 statement 去重后聚类，返回候选 cluster 结构。"""
    by_statement: dict[str, list[dict]] = {}
    for item in items:
        by_statement.setdefault(item["statement"], []).append(item)

    statements = list(by_statement)
    vectors = embedder.encode(statements, progress_label="观点 ")
    labels = cluster_vectors(vectors, threshold=threshold, progress_label="观点 ")

    clusters: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        clusters.setdefault(int(label), []).append(idx)

    ordered = sorted(clusters.values(), key=lambda idx_list: (-len(idx_list), idx_list[0]))
    cluster_payload = []
    for order, member_idx in enumerate(ordered, start=1):
        members = []
        comment_ids: list[str] = []
        for idx in member_idx:
            statement = statements[idx]
            member_comments = [item["comment_id"] for item in by_statement[statement]]
            comment_ids.extend(member_comments)
            members.append(
                {
                    "text": statement,
                    "mention_count": len(member_comments),
                    "comment_ids": sorted(set(member_comments)),
                }
            )
        members.sort(key=lambda m: (-m["mention_count"], m["text"]))
        cluster_payload.append(
            {
                "cluster_id": f"C{order:03d}",
                "statement_count": len(members),
                "mention_count": sum(m["mention_count"] for m in members),
                "comment_ids": sorted(set(comment_ids)),
                "statements": members,
            }
        )

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "embedding_model": config.EMBEDDING_MODEL_REPO,
            "threshold": threshold,
            "statement_count": len(statements),
            "unique_statement_count": len(statements),
            "cluster_count": len(cluster_payload),
        },
        "clusters": cluster_payload,
        "_statements": statements,
        "_vectors": vectors,
    }


def run(*, force: bool = False, paths: Paths | None = None) -> dict:
    paths = paths or default_paths()
    if paths.clusters.exists() and not force:
        LOG.info("候选聚类已存在，直接复用：%s", paths.clusters)
        progress.report("embedding", "复用已有向量", ratio=1.0)
        progress.report("clustering", "复用已有候选聚类", ratio=1.0)
        return read_json(paths.clusters)

    started = time.time()
    items = load_viewpoint_items(paths=paths)
    LOG.info("待聚类观点条目 %d 条", len(items))
    progress.report("embedding", f"正在对 {len(items)} 条观点生成语义向量…")
    embedder = LocalEmbedder()
    result = build_clusters(items, embedder, threshold=config.VIEWPOINT_CLUSTER_THRESHOLD)
    progress.report(
        "embedding",
        f"向量化完成：{result['meta']['statement_count']} 条标准表述",
        ratio=1.0,
    )
    progress.report("clustering", "正在进行观点语义聚类…")

    vectors = result.pop("_vectors")
    statements = result.pop("_statements")
    labels = np.zeros(len(statements), dtype=int)
    cluster_index = {c["cluster_id"]: i for i, c in enumerate(result["clusters"])}
    statement_to_cluster = {}
    for cluster in result["clusters"]:
        for member in cluster["statements"]:
            statement_to_cluster[member["text"]] = cluster_index[cluster["cluster_id"]]
    labels = np.array([statement_to_cluster.get(text, -1) for text in statements], dtype=int)

    np.savez_compressed(
        paths.embeddings,
        statements=np.array(statements, dtype=object),
        vectors=vectors,
        labels=labels,
    )
    write_json(paths.clusters, result)
    LOG.info(
        "聚类完成：%d 条观点 → %d 个候选 cluster（阈值 %.2f），耗时 %.1fs",
        result["meta"]["statement_count"],
        result["meta"]["cluster_count"],
        config.VIEWPOINT_CLUSTER_THRESHOLD,
        time.time() - started,
    )
    progress.report(
        "clustering",
        f"聚类完成：{result['meta']['statement_count']} 条观点 → {result['meta']['cluster_count']} 个候选观点组",
        ratio=1.0,
    )
    return result
