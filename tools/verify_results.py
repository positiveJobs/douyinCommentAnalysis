"""质量检查脚本：逐项校验分析结果（计数、占比、可追溯性等）。

用法：
    uv run python tools/verify_results.py            # 检查 CLI（data/cli）结果
    uv run python tools/verify_results.py <task_id>  # 检查某个 Web 任务结果
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.paths import default_paths, task_paths  # noqa: E402
from analysis.utils import read_json  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CHECKS: list[tuple[str, bool | None, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    """ok 为 None 表示跳过（不计入失败）。"""
    CHECKS.append((name, ok, detail))


def main() -> int:
    task_id = sys.argv[1] if len(sys.argv) > 1 else None
    paths = task_paths(task_id) if task_id else default_paths()
    scope = f"Web 任务 {task_id}" if task_id else "CLI 结果（data/cli）"
    print(f"检查范围：{scope}\n")

    if not paths.results.exists():
        print(f"[FAIL] 找不到分析结果：{paths.results}")
        return 1

    results = read_json(paths.results)
    cleaned = read_json(paths.cleaned)
    extraction = read_json(paths.extraction)
    raw = read_json(paths.raw_comments)
    sample = read_json(paths.sample) or {}

    raw_contents = {str(item.get("content", "")) for item in raw if isinstance(item, dict)}
    cleaned_ids = {comment["comment_id"] for comment in cleaned["comments"]}
    valid_count = results["summary"]["valid_comment_count"]

    check("原始文件字段可解析", len(raw) == cleaned["meta"]["raw_comment_count"], f"raw={len(raw)}")
    check(
        "清洗数量自洽",
        cleaned["meta"]["valid_comment_count"]
        + cleaned["meta"]["invalid_comment_count"]
        + cleaned["meta"]["duplicate_comment_count"]
        == cleaned["meta"]["raw_comment_count"],
        f"有效 {cleaned['meta']['valid_comment_count']} + 无效 {cleaned['meta']['invalid_comment_count']} "
        f"+ 重复 {cleaned['meta']['duplicate_comment_count']} = {cleaned['meta']['raw_comment_count']}",
    )
    check("有效评论条数一致", valid_count == len(cleaned["comments"]), f"valid={valid_count}")

    analysed = {r["comment_id"] for r in extraction["results"]}
    check("提取结果覆盖全部有效评论", analysed == cleaned_ids, f"缺失 {len(cleaned_ids - analysed)} 条")

    viewpoint_ids = [v["viewpoint_id"] for v in results["viewpoints"]]
    names = [v["name"] for v in results["viewpoints"]]
    check("viewpoint_id 唯一", len(set(viewpoint_ids)) == len(viewpoint_ids))
    check("最终观点无同名重复", len(set(names)) == len(names))

    bad_counts, bad_percent, bad_reason, missing_ids, bad_repr = [], [], [], [], []
    for viewpoint in results["viewpoints"]:
        ids = viewpoint["comment_ids"]
        if len(ids) != len(set(ids)) or viewpoint["comment_count"] != len(set(ids)):
            bad_counts.append(viewpoint["viewpoint_id"])
        expected = round(len(set(ids)) / valid_count * 100, 1) if valid_count else 0
        if abs(expected - viewpoint["percentage"]) > 0.05:
            bad_percent.append(viewpoint["viewpoint_id"])
        for comment_id in ids:
            if comment_id not in cleaned_ids:
                missing_ids.append(comment_id)
        for reason in viewpoint["reasons"]:
            if reason["comment_count"] != len(set(reason["comment_ids"])):
                bad_reason.append(reason["reason_id"])
            expected_reason = round(len(set(reason["comment_ids"])) / len(set(ids)) * 100, 1) if ids else 0
            if abs(expected_reason - reason["percentage"]) > 0.05:
                bad_reason.append(reason["reason_id"])
        for text in viewpoint["representative_comments"]:
            if text not in raw_contents:
                bad_repr.append(viewpoint["viewpoint_id"])

    check("观点评论数 = 去重 comment_id 数量", not bad_counts, f"异常: {bad_counts[:5]}")
    check("观点占比计算正确", not bad_percent, f"异常: {bad_percent[:5]}")
    check("理由人数与占比计算正确", not bad_reason, f"异常: {bad_reason[:5]}")
    check("所有 comment_id 可追溯到原始数据", not missing_ids, f"缺失 {len(set(missing_ids))} 条")
    check("代表性评论来自原始评论", not bad_repr, f"异常: {bad_repr[:5]}")

    total_links = sum(v["comment_count"] for v in results["viewpoints"])
    check(
        "观点评论数之和 ≥ 有效评论数（一条评论可含多观点）",
        total_links >= results["summary"]["comment_with_viewpoint_count"],
        f"关联 {total_links} / 有效 {valid_count}",
    )
    check(
        "每个观点至少有一个理由或已标注无理由",
        all(v["reasons"] or v["comment_count"] > 0 for v in results["viewpoints"]),
    )

    accuracy = (sample.get("meta") or {}).get("accuracy")
    if accuracy:
        check(
            "人工抽样校验已产出",
            True,
            f"准确率 {accuracy['accuracy']}%（{accuracy['correct_count']}/{accuracy['judged_count']}）",
        )
    else:
        check(
            "人工抽样校验样本已生成（尚待人工判定，不计入失败）",
            None,
            f"抽样 {sample.get('meta', {}).get('sample_size', 0)} 条，"
            "填写 manual_sample_check_judged.json 后可计算准确率",
        )

    failures = [item for item in CHECKS if item[1] is False]
    for name, ok, detail in CHECKS:
        status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
        print(f"[{status}] {name}" + (f" —— {detail}" if detail else ""))
    skipped = sum(1 for item in CHECKS if item[1] is None)
    print(
        f"\n共 {len(CHECKS)} 项检查，通过 {len(CHECKS) - len(failures) - skipped} 项，"
        f"跳过 {skipped} 项，失败 {len(failures)} 项"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
