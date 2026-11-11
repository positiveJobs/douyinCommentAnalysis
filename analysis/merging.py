"""阶段 5：LLM 对候选 cluster 进行归并/拆分/命名（Prompt B），并做全局去重。"""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from datetime import datetime
from typing import Any, Iterable

import numpy as np

from . import config
from . import cancel
from . import progress
from .clustering import cluster_vectors, load_viewpoint_items
from .embedding import LocalEmbedder
from .llm import LLMClient, LLMError
from .paths import Paths, default_paths
from .utils import LOG, read_json, write_json

MERGE_PROMPT_VERSION = "B-v1"
CONSOLIDATE_PROMPT_VERSION = "C-v1"

SYSTEM_MERGE_VIEWPOINTS = """你是中文观点归并专家。输入是同一话题下由向量聚类得到的候选观点分组，分组可能过粗（不同观点被混在一起）也可能过细（同一观点有多个表述）。
规则：
1. 判断组内不同表述是否真正表达同一个核心意思：是则合并，否则拆分，并允许把条目在不同观点之间重新分配。
2. 不能只因为出现相同关键词就合并，例如“先别急着进厂”与“进厂可以，但要选择正规工厂”是不同观点，必须区分。
3. 也不要过度拆分：核心意思相同的不同措辞（例如“别急着进厂”“年轻可以多试试”“先找适合自己的工作”）必须放在同一个观点里。
4. 每个最终观点输出：name（不超过 30 字、可以独立理解、体现立场方向的观点名）、description（40 字以内的一句说明）、stance（support/oppose/conditional/neutral/unclear，表示该观点整体主张的倾向）。
5. 输入中的每个条目必须且只能被分配到一个观点，item_ids 必须覆盖全部输入条目，不得编造 item_id。
6. 只输出 JSON，不要输出解释或 Markdown：{"viewpoints":[{"name":"...","description":"...","stance":"support","item_ids":["I0001"]}]}"""

SYSTEM_MERGE_REASONS = """你是中文观点理由归并专家。输入是某个观点下由向量聚类得到的候选理由分组，可能过粗或过细。
规则：
1. 判断组内不同表述是否表达同一个理由：是则合并，否则拆分，并允许跨组重新分配条目。
2. 不要过度拆分：例如“流水线太累”“工厂干活很辛苦”“进厂劳动强度大”应归为同一个理由。
3. 也不要过度合并：例如“工厂工作强度大”和“工厂长期发展空间有限”是两个不同理由。
4. 每个最终理由输出：name（不超过 20 字、简洁、可比较）、description（30 字以内的补充说明，可为空字符串）。
5. 输入中的每个条目必须且只能被分配到一个理由，item_ids 必须覆盖全部输入条目，不得编造 item_id。
6. 只输出 JSON，不要输出解释或 Markdown：{"viewpoints":[{"name":"...","description":"...","item_ids":["I0001"]}]}"""

SYSTEM_CONSOLIDATE = """你是中文观点归纳专家。输入是同一话题下已经初步归并过的观点清单（每条含观点名、说明、条目编号）。
任务：把这些观点归纳为“核心观点”。规则：
1. 核心意思是同一个意见的观点必须合并，例如“不建议进厂”“进厂容易毁掉人”“年纪小进厂容易被骗”属于同一个核心观点。
2. 立场相反的观点必须分开，例如“不建议进厂”与“经济困难时可以先进厂过渡”不能合并。如果某个核心观点同时容纳了支持进厂和反对进厂的意见，说明合并过度，必须拆开。
3. 不要把具体意见并入笼统的“视情况而定/灵活决策/综合评估”类观点：例如“反对直接进厂，建议先积累阅历”必须保持为独立的“反对直接进厂”核心观点，不能被“根据经济状况灵活决策”吸收。
4. 关注点明显不同的观点保持分开，例如“进厂要与异性保持距离”和“建议去大城市工作”不是同一个观点。
5. 数量服从语义：不要为了减少数量而强行合并，也不要为了凑数强行拆分；相同意思的不同措辞必须合并。
6. name 是不超过 25 字的标准观点名，必须能独立理解并体现立场方向；description 是 40 字以内的说明。
7. 输入中的每个条目必须且只能归入一个核心观点，item_ids 必须覆盖全部输入条目，不得编造 item_id。
8. 只输出 JSON，不要输出解释或 Markdown：{"viewpoints":[{"name":"...","description":"...","item_ids":["N0001"]}]}"""

SYSTEM_CONSOLIDATE_REASONS = """你是中文理由归纳专家。输入是同一个观点下已经初步归并过的理由清单（每条含理由名、说明、条目编号）。
任务：把这些理由归纳为“主要理由”。规则：
1. 表达同一个理由的不同措辞必须合并，例如“工作强度大”“干活太累”“劳动强度高”属于同一个理由。
2. 指向不同原因的保持分开，例如“工厂工作强度大”和“工厂没有发展空间”是两个不同理由。
3. 禁止输出“其他/未归类/综合因素/个人情况不同”这类笼统条目：必须拆开并归入最接近的具体理由。
4. name 不超过 15 字，简洁、可比较；description 为 30 字以内的说明，可为空字符串。
5. 输入中的每个条目必须且只能归入一个理由，item_ids 必须覆盖全部输入条目，不得编造 item_id。
6. 只输出 JSON，不要输出解释或 Markdown：{"viewpoints":[{"name":"...","description":"","item_ids":["N0001"]}]}"""

VALID_STANCES = ("support", "oppose", "conditional", "neutral", "unclear")

# 兜底桶名字：这些不是真正的观点/理由，需要解散后按语义归入最接近的条目
JUNK_NAME_RE = re.compile(r"其他|未归类|未分类|无法归类|零散|杂项|综合因素|说不清")


def _normalize_stance(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in VALID_STANCES else "unclear"


def _chunk_groups(
    groups: list[dict],
    *,
    group_limit: int = config.MERGE_CHUNK_GROUP_LIMIT,
    char_budget: int = config.MERGE_CHUNK_CHAR_BUDGET,
    item_limit: int = config.MERGE_CHUNK_ITEM_LIMIT,
) -> list[list[dict]]:
    """把候选组切成若干 chunk：单组过大时按条目上限再切分。"""
    flat: list[dict] = []
    for group in groups:
        items = group["items"]
        if len(items) <= item_limit and sum(len(i["text"]) for i in items) <= char_budget:
            flat.append(group)
            continue
        for start in range(0, len(items), item_limit):
            piece = items[start : start + item_limit]
            if not piece:
                continue
            flat.append(
                {
                    "group_id": f"{group['group_id']}-{start // item_limit + 1}",
                    "items": piece,
                }
            )

    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    total_items = 0
    for group in flat:
        group_chars = sum(len(i["text"]) for i in group["items"]) + 20
        if current and (
            len(current) >= group_limit
            or size + group_chars > char_budget
            or total_items + len(group["items"]) > item_limit
        ):
            chunks.append(current)
            current = []
            size = 0
            total_items = 0
        current.append(group)
        size += group_chars
        total_items += len(group["items"])
    if current:
        chunks.append(current)
    return chunks


def _build_chunk_prompt(chunk: list[dict], item_count: int) -> str:
    lines = ["以下是候选分组："]
    for group in chunk:
        lines.append(f"\n[{group['group_id']}]")
        for item in group["items"]:
            lines.append(f"- {item['item_id']} {item['text']}")
    lines.append(
        f"\n请对以上 {item_count} 个条目输出最终分组 JSON，"
        "item_ids 必须覆盖全部条目且不重复，不要编造 item_id。"
    )
    return "\n".join(lines)


def _parse_merge_payload(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("viewpoints", "groups", "reasons", "results", "clusters"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = []
    if not isinstance(payload, list):
        return []
    parsed: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("statement") or item.get("title") or "").strip()
        if not name:
            continue
        ids = item.get("item_ids") or item.get("ids") or item.get("members") or []
        if isinstance(ids, str):
            ids = [ids]
        parsed.append(
            {
                "name": name[:60],
                "description": str(item.get("description") or "").strip()[:200],
                "stance": _normalize_stance(item.get("stance")),
                "item_ids": [str(i) for i in ids],
            }
        )
    return parsed


def merge_chunk(
    client: LLMClient,
    chunk: list[dict],
    *,
    include_stance: bool,
    kind: str,
    embedder: LocalEmbedder | None = None,
) -> list[dict]:
    """对一个 chunk 调用 LLM 做归并/命名，并做 Python 侧校验与兜底。"""
    system_prompt = SYSTEM_MERGE_VIEWPOINTS if include_stance else SYSTEM_MERGE_REASONS
    item_ids = [item["item_id"] for group in chunk for item in group["items"]]
    text_by_id = {item["item_id"]: item["text"] for group in chunk for item in group["items"]}
    valid_ids = set(item_ids)
    prompt = _build_chunk_prompt(chunk, len(item_ids))

    parsed: list[dict] = []
    for attempt in range(2):
        try:
            payload, _ = client.chat_json(system_prompt, prompt)
            parsed = _parse_merge_payload(payload)
            if parsed:
                break
        except LLMError as exc:
            LOG.warning("归并调用失败（%s，第 %d 次）：%s", kind, attempt + 1, exc)
        if attempt == 0:
            time.sleep(3)

    if not parsed:
        # 调用失败或输出为空：按候选组直接生成，避免把整批塞进“其他”桶
        fallback = [
            {
                "name": group["items"][0]["text"][:30],
                "description": "",
                "stance": "unclear",
                "item_ids": [item["item_id"] for item in group["items"]],
            }
            for group in chunk
            if group["items"]
        ]
        return fallback

    # 校验：item_id 合法性 + 每个条目只能出现一次
    assigned: set[str] = set()
    cleaned: list[dict] = []
    for viewpoint in parsed:
        ids = [i for i in viewpoint["item_ids"] if i in valid_ids and i not in assigned]
        if not ids:
            continue
        assigned.update(ids)
        cleaned.append({**viewpoint, "item_ids": ids})

    # 解散“其他/未归类”这类兜底桶，按语义并入最接近的条目
    junk_ids = [i for entry in cleaned if JUNK_NAME_RE.search(entry["name"]) for i in entry["item_ids"]]
    if junk_ids and embedder is not None:
        cleaned = [entry for entry in cleaned if not JUNK_NAME_RE.search(entry["name"])]
        assigned.difference_update(junk_ids)

    # LLM 漏掉的条目：按组归并回已有观点，避免信息丢失
    unassigned = [i for i in item_ids if i not in assigned]
    if unassigned and embedder is not None and cleaned:
        _assign_leftovers(embedder, cleaned, {i: text_by_id[i] for i in unassigned})
        return cleaned
    if unassigned:
        LOG.warning("归并结果缺少 %d/%d 个条目，用向量兜底分配", len(unassigned), len(item_ids))
        fallback_name = "其他表述"
        cleaned.append(
            {
                "name": fallback_name,
                "description": "LLM 归并未覆盖的表述",
                "stance": "unclear",
                "item_ids": unassigned,
            }
        )
    return cleaned


def merge_groups(
    client: LLMClient,
    groups: list[dict],
    *,
    include_stance: bool,
    kind: str,
    embedder: LocalEmbedder | None = None,
    stage: str = "viewpoint_merge",
) -> list[dict]:
    """对候选组做 chunk 化归并，返回最终条目（item_ids 指向传入的 item）。"""
    if not groups:
        return []
    chunks = _chunk_groups(groups)
    LOG.info("%s 归并：%d 个候选组 → %d 个 chunk（并发 %d）", kind, len(groups), len(chunks), client.concurrency)
    merge_started = time.monotonic()
    progress.report(
        stage,
        f"正在归并候选观点：0 / {len(chunks)} 批（并发 {client.concurrency}）",
        current=0,
        total=len(chunks),
    )
    outcomes = client.map_concurrent(
        lambda payload: merge_chunk(
            client,
            payload[1],
            include_stance=include_stance,
            kind=f"{kind}-{payload[0]}/{len(chunks)}",
            embedder=embedder,
        ),
        list(enumerate(chunks, start=1)),
        label=f"{kind} 归并",
    )
    results: list[dict] = []
    for index, outcome in enumerate(outcomes, start=1):
        cancel.check(f"观点归并已取消（已完成 {index - 1}/{len(chunks)} 批）")
        if isinstance(outcome, Exception):
            LOG.warning("%s 归并 chunk 失败：%s", kind, outcome)
            continue
        results.extend(outcome)
        progress.report(
            stage,
            progress.progress_text(index, len(chunks), merge_started, unit="批", prefix="正在归并候选观点 "),
            current=index,
            total=len(chunks),
        )
    return results


def dedupe_by_embedding(
    client: LLMClient,
    embedder: LocalEmbedder,
    items: list[dict],
    *,
    threshold: float,
    include_stance: bool,
    kind: str,
) -> list[dict]:
    """对已命名的条目按名称向量聚类，再把同簇条目交给 LLM 合并（跨 chunk 去重）。"""
    if len(items) <= 1:
        return items
    texts = [f"{item['name']}：{item.get('description', '')}".strip("：") for item in items]
    vectors = embedder.encode(texts, progress_label=f"{kind}名称 ", stage="viewpoint_merge")
    labels = cluster_vectors(vectors, threshold=threshold, stage="viewpoint_merge")
    buckets: dict[int, list[dict]] = {}
    for item, label in zip(items, labels):
        buckets.setdefault(int(label), []).append(item)

    merged_output: list[dict] = []
    merged_clusters = 0
    id_to_member: dict[str, dict] = {}
    dedupe_groups: list[dict] = []
    for members in buckets.values():
        if len(members) == 1:
            merged_output.append({**members[0], "members": [members[0]]})
            continue
        merged_clusters += 1
        # 把同簇条目放进同一个候选组，交由 LLM 决定合并还是保留差异
        flat_items = []
        for idx, member in enumerate(members):
            item_id = f"M{len(id_to_member):04d}"
            id_to_member[item_id] = member
            text = member["name"]
            if member.get("description"):
                text = f"{text}（{member['description']}）"
            flat_items.append({"item_id": item_id, "text": text})
        dedupe_groups.append({"group_id": f"D{len(dedupe_groups) + 1:04d}", "items": flat_items})

    if dedupe_groups:
        results = merge_groups(client, dedupe_groups, include_stance=include_stance, kind=f"{kind}-dedupe")
    else:
        results = []
    for entry in results:
        members_in_entry = [id_to_member[i] for i in entry["item_ids"] if i in id_to_member]
        if not members_in_entry:
            continue
        merged_entry = {
            "name": entry["name"],
            "description": entry["description"],
            "stance": entry.get("stance", members_in_entry[0].get("stance", "unclear")),
            "statement_item_ids": sorted(
                {sid for member in members_in_entry for sid in member.get("statement_item_ids", [])}
            ),
            "cluster_ids": sorted({c for member in members_in_entry for c in member.get("cluster_ids", [])}),
            "merged_from": [member["name"] for member in members_in_entry],
            "members": members_in_entry,
        }
        merged_output.append(merged_entry)
    LOG.info("%s 全局去重：%d 组被合并", kind, merged_clusters)
    return merged_output


def _call_grouping_items(
    client: LLMClient,
    system_prompt: str,
    keyed_items: list[tuple[str, str]],
    extra_note: str,
) -> tuple[list[dict], list[str]]:
    """调用 LLM 对 (item_id, text) 列表做分组，返回 (分组结果, 未分配条目)。"""
    prompt = "\n".join(f"- {item_id} {text}" for item_id, text in keyed_items) + "\n\n" + extra_note
    entries: list[dict] = []
    try:
        payload, _ = client.chat_json(system_prompt, prompt)
        entries = _parse_merge_payload(payload)
    except LLMError as exc:
        LOG.warning("归纳调用失败：%s", exc)

    valid_ids = {item_id for item_id, _ in keyed_items}
    assigned: set[str] = set()
    cleaned: list[dict] = []
    for entry in entries:
        ids = [i for i in entry["item_ids"] if i in valid_ids and i not in assigned]
        if not ids:
            continue
        assigned.update(ids)
        cleaned.append({**entry, "item_ids": ids})
    leftover = [item_id for item_id, _ in keyed_items if item_id not in assigned]
    return cleaned, leftover


def _assign_leftovers(
    embedder: LocalEmbedder,
    entries: list[dict],
    leftover_texts: dict[str, str],
) -> None:
    """把 LLM 未覆盖的条目按向量相似度并入最接近的观点。"""
    if not leftover_texts or not entries:
        return
    texts = list(leftover_texts.values())
    entry_names = [f"{entry['name']}：{entry.get('description', '')}".strip("：") for entry in entries]
    vectors = embedder.encode(texts + entry_names, progress_label="兜底分配 ", stage="viewpoint_merge")
    item_vectors = vectors[: len(texts)]
    entry_vectors = vectors[len(texts) :]
    similarity = item_vectors @ entry_vectors.T
    for idx, item_id in enumerate(leftover_texts):
        entries[int(similarity[idx].argmax())]["item_ids"].append(item_id)


def _group_items_once(
    client: LLMClient,
    embedder: LocalEmbedder,
    items: list[dict],
    *,
    kind: str,
    chunk_size: int,
    ratio: float,
    system_prompt: str = SYSTEM_CONSOLIDATE,
) -> list[dict]:
    """一轮归纳：把 items 按 chunk 送 LLM 归并成更少的核心观点。"""
    chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    def work(payload: tuple[int, list[dict]]) -> list[dict]:
        index, chunk = payload
        keyed: list[tuple[str, str]] = []
        for pos, item in enumerate(chunk):
            text = item["name"]
            if item.get("description"):
                text = f"{text}（{item['description']}）"
            keyed.append((f"N{pos:04d}", text))
        target = max(config.CONSOLIDATE_MIN_TARGET, math.ceil(len(chunk) * ratio))
        extra_note = (
            f"本批共 {len(chunk)} 条观点。若其中确实存在同义表述，请合并后尽量控制在 {target} 个核心观点以内；"
            "语义确实不同的观点必须保留，不要强行合并；item_ids 必须覆盖全部条目且不重复。"
        )
        entries, leftover = _call_grouping_items(client, system_prompt, keyed, extra_note)
        # “其他/未分类”只是兜底桶，不是真正的观点：解散后按语义并入最接近的观点
        dissolved = [
            item_id for entry in entries if JUNK_NAME_RE.search(entry["name"]) for item_id in entry["item_ids"]
        ]
        if dissolved:
            entries = [entry for entry in entries if not JUNK_NAME_RE.search(entry["name"])]
            leftover = sorted(set(leftover) | set(dissolved))
        if leftover:
            _assign_leftovers(embedder, entries, {iid: dict(keyed)[iid] for iid in leftover})
        if not entries:
            entries = [
                {"name": item["name"], "description": item.get("description", ""), "item_ids": [item_id]}
                for item_id, item in zip([k for k, _ in keyed], chunk)
            ]
        LOG.info("%s chunk %d/%d：%d 条 → %d 个核心观点", kind, index + 1, len(chunks), len(chunk), len(entries))
        merged = []
        id_to_item = {item_id: item for item_id, item in zip([k for k, _ in keyed], chunk)}
        for entry in entries:
            members = [id_to_item[i] for i in entry["item_ids"] if i in id_to_item]
            if not members:
                continue
            merged.append(
                {
                    "name": entry["name"],
                    "description": entry.get("description", ""),
                    "stance": entry.get("stance", "unclear"),
                    "members": [m for member in members for m in member.get("members", [member])],
                }
            )
        return merged

    outcomes = client.map_concurrent(work, list(enumerate(chunks)), label=f"{kind} 归纳")
    results: list[dict] = []
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            LOG.warning("%s 归纳失败：%s", kind, outcome)
            continue
        results.extend(outcome)
    return results


def consolidate_viewpoints(
    client: LLMClient,
    embedder: LocalEmbedder,
    items: list[dict],
    *,
    target_max: int = config.CORE_VIEWPOINT_TARGET_MAX,
) -> list[dict]:
    """多轮归纳，把细粒度观点整理成核心观点（每轮只缩小范围，不做统计）。"""
    current = items
    for round_index in range(config.CONSOLIDATE_MAX_ROUNDS):
        if len(current) <= target_max:
            break
        LOG.info("第 %d 轮核心观点归纳：当前 %d 个观点", round_index + 1, len(current))
        progress.report(
            "viewpoint_merge",
            f"第 {round_index + 1} 轮核心观点归纳：当前 {len(current)} 个候选观点",
            ratio=round_index / max(config.CONSOLIDATE_MAX_ROUNDS, 1),
        )
        current = _group_items_once(
            client,
            embedder,
            current,
            kind=f"核心观点-第{round_index + 1}轮",
            chunk_size=config.CONSOLIDATE_CHUNK_ITEM_LIMIT,
            ratio=config.CONSOLIDATE_TARGET_RATIO,
        )
    return current


def consolidate_reasons(
    client: LLMClient,
    embedder: LocalEmbedder,
    items: list[dict],
    *,
    target_max: int = config.REASON_TARGET_MAX,
) -> list[dict]:
    """把某个观点下过细的理由归纳为主要理由。"""
    current = items
    for round_index in range(config.REASON_CONSOLIDATE_MAX_ROUNDS):
        if len(current) <= target_max:
            break
        current = _group_items_once(
            client,
            embedder,
            current,
            kind=f"理由归纳-第{round_index + 1}轮",
            chunk_size=config.REASON_CONSOLIDATE_CHUNK_LIMIT,
            ratio=config.REASON_CONSOLIDATE_RATIO,
            system_prompt=SYSTEM_CONSOLIDATE_REASONS,
        )
    return current


def ensure_members(items: list[dict]) -> list[dict]:
    """保证每个条目都带 members 字段（细粒度观点本身就是自己的成员）。

    小数据集可能直接跳过归纳轮次，此时条目就是细粒度观点本身，需要在这里补齐，
    否则最终合并阶段会取不到对应的评论，导致结果为 0 个观点。
    """
    return [item if item.get("members") else {**item, "members": [item]} for item in items]


def repair_core_assignments(
    embedder: LocalEmbedder,
    cores: list[dict],
    *,
    reassign_margin: float = 0.05,
    split_share: float = 0.5,
    split_similarity: float = 0.6,
    rename_share: float = 0.7,
) -> list[dict]:
    """用向量一致性修正核心观点归属（纯 Python，不调用 LLM）。

    两类修正：
    1. 某个子观点与其它核心观点更接近时，把它移到更接近的核心观点；
    2. 某个核心观点中单个子观点占了一半以上评论、且与核心观点名相似度很低时，
       说明它被错误吸收，拆分为独立核心观点。
    """
    cores = ensure_members(cores)
    if len(cores) <= 1:
        return cores
    core_texts = [f"{core['name']}：{core.get('description', '')}".strip("：") for core in cores]
    core_vectors = embedder.encode(core_texts, progress_label="核心观点名 ", stage="viewpoint_merge")
    members = [(index, member) for index, core in enumerate(cores) for member in core.get("members", [])]
    if not members:
        return cores
    member_texts = [f"{member['name']}：{member.get('description', '')}".strip("：") for _, member in members]
    member_vectors = embedder.encode(member_texts, progress_label="子观点名 ", stage="viewpoint_merge")
    similarity = member_vectors @ core_vectors.T

    buckets: list[list[int]] = [[] for _ in cores]
    for position, (core_index, _member) in enumerate(members):
        buckets[core_index].append(position)

    moved = 0
    for position, (core_index, _member) in enumerate(members):
        best = int(similarity[position].argmax())
        if (
            best != core_index
            and similarity[position][best] >= similarity[position][core_index] + reassign_margin
            and len(buckets[core_index]) > 1
        ):
            buckets[core_index].remove(position)
            buckets[best].append(position)
            moved += 1
    if moved:
        LOG.info("核心观点归属修正：移动 %d 个子观点", moved)

    repaired: list[dict] = []
    for core, bucket in zip(cores, buckets):
        bucket = sorted(bucket)
        if not bucket:
            continue
        core_members = [members[position][1] for position in bucket]
        total_comments = sum(len(member["comment_ids"]) for member in core_members)
        dominant_position = max(bucket, key=lambda position: len(members[position][1]["comment_ids"]))
        dominant = members[dominant_position][1]
        dominant_share = len(dominant["comment_ids"]) / total_comments if total_comments else 0
        if (
            len(bucket) > 1
            and dominant_share >= split_share
            and similarity[dominant_position][cores.index(core)] < split_similarity
        ):
            LOG.info(
                "拆分核心观点「%s」：子观点「%s」占 %.0f%% 且与名称不匹配，独立成观点",
                core["name"],
                dominant["name"],
                dominant_share * 100,
            )
            repaired.append(
                {
                    "name": dominant["name"],
                    "description": dominant.get("description", ""),
                    "stance": core.get("stance", "unclear"),
                    "members": [dominant],
                }
            )
            rest = [members[position][1] for position in bucket if position != dominant_position]
            if rest:
                repaired.append({**core, "members": rest})
            continue
        if len(bucket) > 1 and dominant_share >= rename_share:
            # 一个子观点占据绝大多数评论时，用它的名称更有信息量
            repaired.append(
                {
                    **core,
                    "name": dominant["name"],
                    "description": core.get("description") or dominant.get("description", ""),
                    "members": core_members,
                }
            )
            continue
        repaired.append({**core, "members": core_members})
    return repaired


def run(*, force: bool = False, paths: Paths | None = None) -> dict:
    """执行观点归并与命名，输出 final_viewpoints.json。"""
    config.require_llm_config()
    paths = paths or default_paths()
    if paths.final_viewpoints.exists() and not force:
        LOG.info("最终观点文件已存在，直接复用：%s", paths.final_viewpoints)
        progress.report("viewpoint_merge", "复用已有关键观点结果", ratio=1.0)
        return read_json(paths.final_viewpoints)

    started = time.time()
    clusters = read_json(paths.clusters)
    if not clusters:
        raise RuntimeError("请先运行聚类（viewpoint_clusters.json 不存在）")
    items = load_viewpoint_items(paths=paths)

    # statement -> 该表述对应的评论、立场、理由
    statement_meta: dict[str, dict] = {}
    for item in items:
        meta = statement_meta.setdefault(
            item["statement"],
            {"comment_ids": [], "stances": [], "reasons": []},
        )
        meta["comment_ids"].append(item["comment_id"])
        meta["stances"].append(item["stance"])
        if item["reason"]:
            meta["reasons"].append(item["reason"])

    # 构建全局 item 注册表（每个标准表述一个 item）
    registry: dict[str, dict] = {}
    groups: list[dict] = []
    for cluster in clusters["clusters"]:
        group_items = []
        for member in cluster["statements"]:
            item_id = f"I{len(registry) + 1:05d}"
            meta = statement_meta.get(member["text"], {"comment_ids": member["comment_ids"], "stances": [], "reasons": []})
            registry[item_id] = {
                "text": member["text"],
                "comment_ids": sorted(set(meta["comment_ids"]) or set(member["comment_ids"])),
                "stances": meta["stances"],
                "reasons": meta["reasons"],
                "cluster_id": cluster["cluster_id"],
            }
            group_items.append({"item_id": item_id, "text": member["text"]})
        groups.append({"group_id": cluster["cluster_id"], "items": group_items})

    LOG.info(
        "观点归并：%d 个候选 cluster / %d 条标准表述，文本上限 %d 字符/次",
        len(groups),
        len(registry),
        config.MERGE_CHUNK_CHAR_BUDGET,
    )

    def expand(entry: dict) -> dict | None:
        """把归并条目展开为：涉及的标准表述、评论、立场。"""
        statement_ids = entry.get("statement_item_ids", [])
        comment_ids: set[str] = set()
        stances: list[str] = []
        statements = []
        cluster_ids = set()
        for item_id in statement_ids:
            meta = registry.get(item_id)
            if not meta:
                continue
            comment_ids.update(meta["comment_ids"])
            stances.extend(meta["stances"])
            cluster_ids.add(meta["cluster_id"])
            statements.append({"text": meta["text"], "comment_ids": meta["comment_ids"]})
        if not comment_ids:
            return None
        stance_counter = Counter(stances)
        majority_stance = (
            stance_counter.most_common(1)[0][0] if stance_counter else _normalize_stance(entry.get("stance"))
        )
        return {
            "name": entry["name"],
            "description": entry.get("description", ""),
            "stance": majority_stance,
            "stance_distribution": dict(stance_counter),
            "statements": statements,
            "cluster_ids": sorted(cluster_ids),
            "comment_ids": sorted(comment_ids),
            "merged_from": entry.get("merged_from", []),
            "statement_item_ids": statement_ids,
        }

    with LLMClient() as client:
        embedder = LocalEmbedder()
        merged = merge_groups(client, groups, include_stance=True, kind="viewpoint", embedder=embedder)
        LOG.info("chunk 内归并得到 %d 个观点，开始跨 cluster 去重", len(merged))

        # 转成可去重的条目（保留原始 statement item）
        dedupe_items = []
        for entry in merged:
            statement_ids = [i for i in entry["item_ids"] if i in registry]
            if not statement_ids:
                continue
            dedupe_items.append(
                {
                    "name": entry["name"],
                    "description": entry["description"],
                    "stance": entry["stance"],
                    "statement_item_ids": statement_ids,
                    "cluster_ids": sorted({registry[i]["cluster_id"] for i in statement_ids}),
                }
            )

        fine_items = dedupe_by_embedding(
            client,
            embedder,
            dedupe_items,
            threshold=config.VIEWPOINT_GLOBAL_MERGE_THRESHOLD,
            include_stance=True,
            kind="viewpoint",
        )

        # 细粒度观点（保留全部信息，作为中间结果）
        fine_viewpoints = []
        for entry in fine_items:
            expanded = expand(entry)
            if expanded:
                fine_viewpoints.append(expanded)
        fine_viewpoints.sort(key=lambda v: (-len(v["comment_ids"]), v["name"]))
        for order, viewpoint in enumerate(fine_viewpoints, start=1):
            viewpoint["detail_id"] = f"D{order:03d}"
        write_json(
            paths.merge_detail,
            {
                "meta": {
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "viewpoint_count": len(fine_viewpoints),
                    "note": "细粒度观点（已按语义归并，供追溯使用）",
                },
                "viewpoints": fine_viewpoints,
            },
        )
        LOG.info("细粒度观点 %d 个，写入 %s", len(fine_viewpoints), paths.merge_detail.name)

        # 核心观点归纳：多轮归并，得到报告使用的最终观点
        core_items = ensure_members(consolidate_viewpoints(client, embedder, fine_viewpoints))
        core_items = repair_core_assignments(embedder, core_items)
        llm_stats = dict(client.stats)

    viewpoints = []
    for core in core_items:
        members: list[dict] = [member for member in core.get("members", []) if member.get("comment_ids")]
        if not members:
            continue
        comment_ids: set[str] = set()
        statements: list[dict] = []
        cluster_ids: set[str] = set()
        stances: list[str] = []
        for member in members:
            comment_ids.update(member["comment_ids"])
            statements.extend(member["statements"])
            cluster_ids.update(member["cluster_ids"])
            stances.extend(
                stance
                for stance, count in (member.get("stance_distribution") or {}).items()
                for _ in range(count)
            )
        stance_counter = Counter(stances)
        viewpoints.append(
            {
                "name": core["name"],
                "description": core.get("description", ""),
                "stance": stance_counter.most_common(1)[0][0] if stance_counter else "unclear",
                "stance_distribution": dict(stance_counter),
                "statements": statements,
                "cluster_ids": sorted(cluster_ids),
                "comment_ids": sorted(comment_ids),
                "sub_viewpoints": [
                    {
                        "detail_id": member["detail_id"],
                        "name": member["name"],
                        "description": member.get("description", ""),
                        "comment_count": len(member["comment_ids"]),
                        "comment_ids": member["comment_ids"],
                    }
                    for member in sorted(members, key=lambda m: (-len(m["comment_ids"]), m["name"]))
                ],
                "merged_from": [member["name"] for member in members],
            }
        )

    viewpoints.sort(key=lambda v: (-len(v["comment_ids"]), v["name"]))
    for order, viewpoint in enumerate(viewpoints, start=1):
        viewpoint["viewpoint_id"] = f"V{order:03d}"

    output = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "model": config.CHAT_MODEL,
            "prompt_version": MERGE_PROMPT_VERSION,
            "consolidate_prompt_version": CONSOLIDATE_PROMPT_VERSION,
            "cluster_threshold": config.VIEWPOINT_CLUSTER_THRESHOLD,
            "global_merge_threshold": config.VIEWPOINT_GLOBAL_MERGE_THRESHOLD,
            "candidate_cluster_count": len(clusters["clusters"]),
            "statement_count": len(registry),
            "fine_viewpoint_count": len(fine_viewpoints),
            "viewpoint_count": len(viewpoints),
            "llm_stats": llm_stats,
            "elapsed_seconds": round(time.time() - started, 1),
        },
        "viewpoints": viewpoints,
    }
    write_json(paths.final_viewpoints, output)
    LOG.info("最终观点 %d 个，耗时 %.1fs", len(viewpoints), time.time() - started)
    progress.report("viewpoint_merge", f"观点归并完成：{len(viewpoints)} 个核心观点", ratio=1.0)
    return output
