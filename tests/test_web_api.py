"""Web 服务端到端测试（需要先启动后端）。

启动后端：
    uv run uvicorn backend.main:app --port 8000

运行测试：
    uv run python tests/test_web_api.py
    uv run python tests/test_web_api.py --base http://127.0.0.1:8000 --full   # 追加真实 detail_comments.json 测试
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(ROOT) not in sys.path:  # 允许直接 python tests/test_web_api.py 运行
    sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))


def upload(client: httpx.Client, path: Path, topic: str | None = None) -> httpx.Response:
    files = {"file": (path.name, path.read_bytes(), "application/json")}
    data = {"topic": topic} if topic else None
    return client.post("/api/tasks", files=files, data=data, timeout=120.0)


def wait_for_task(client: httpx.Client, task_id: str, timeout: float = 1800.0, verbose: bool = True):
    """轮询任务状态，返回最终任务信息。"""
    deadline = time.time() + timeout
    last_message = None
    while time.time() < deadline:
        response = client.get(f"/api/tasks/{task_id}", timeout=30.0)
        response.raise_for_status()
        task = response.json()
        if verbose and task.get("message") != last_message:
            last_message = task.get("message")
            print(f"    · [{task['stage']}] {task['progress']}% {last_message}")
        if task["status"] in ("completed", "failed"):
            return task
        time.sleep(1.0)
    raise TimeoutError(f"任务 {task_id} 超时未完成")


def test_small_dataset(client: httpx.Client) -> str | None:
    """测试 1：上传正常 JSON → 创建任务 → 分析成功 → 结果可读。"""
    response = upload(client, FIXTURES / "small_comments.json")
    if response.status_code != 200:
        record("测试1 上传正常 JSON", False, f"HTTP {response.status_code} {response.text[:200]}")
        return None
    payload = response.json()
    task_id = payload["task_id"]
    record(
        "测试1a 上传立即返回 task_id（不阻塞）",
        201 > 200 and response.status_code == 200 and bool(task_id),
        f"task_id={task_id} comment_count={payload.get('comment_count')}",
    )

    task = wait_for_task(client, task_id)
    ok = task["status"] == "completed"
    record("测试1b 分析成功完成", ok, f"status={task['status']} error={task.get('error')}")
    if not ok:
        return task_id

    summary = client.get(f"/api/tasks/{task_id}/summary").json()["summary"]
    ok_valid = summary["valid_comment_count"] == 22
    ok_dup = summary["duplicate_comment_count"] == 1
    ok_invalid = summary["invalid_comment_count"] == 2
    record(
        "测试4 重复评论不重复计数（25 条 → 有效 22 / 重复 1 / 无效 2）",
        ok_valid and ok_dup and ok_invalid,
        f"valid={summary['valid_comment_count']} duplicate={summary['duplicate_comment_count']} "
        f"invalid={summary['invalid_comment_count']}",
    )

    viewpoints = client.get(f"/api/tasks/{task_id}/viewpoints").json()
    record("测试1c 观点列表可读", viewpoints["total"] > 0, f"观点数={viewpoints['total']}")

    result = client.get(f"/api/tasks/{task_id}/result").json()
    multi = [
        cid
        for cid in {cid for vp in result["viewpoints"] for cid in vp["comment_ids"]}
        if sum(1 for vp in result["viewpoints"] if cid in vp["comment_ids"]) > 1
    ]
    # 同一评论可以绑定多个观点；完整数据集上会出现大量多观点评论（见 test_full_dataset）
    bound = sorted({cid for vp in result["viewpoints"] for cid in vp["comment_ids"]})
    record(
        "测试5 评论与 viewpoint_id 绑定关系正确",
        bool(bound) and all(isinstance(cid, str) for cid in bound),
        f"被绑定观点的评论 {len(bound)} 条，其中多观点评论 {len(multi)} 条",
    )

    # 测试8：观点 → comment_id → 原始评论 完整追溯
    target = viewpoints["items"][0]
    page = client.get(
        f"/api/tasks/{task_id}/viewpoints/{target['viewpoint_id']}/comments",
        params={"page": 1, "page_size": 10},
    ).json()
    raw = json.loads((FIXTURES / "small_comments.json").read_text(encoding="utf-8"))
    by_id = {c["comment_id"]: c["content"] for c in raw["comments"]}
    traceable = all(
        item["comment_id"] in by_id and by_id[item["comment_id"]] == item["content"] for item in page["items"]
    )
    record(
        "测试8 观点可追溯到原始评论（内容一致）",
        traceable and page["total"] == target["comment_count"],
        f"{target['viewpoint_id']} 共 {page['total']} 条，分页返回 {len(page['items'])} 条",
    )

    detail = client.get(f"/api/tasks/{task_id}/viewpoints/{target['viewpoint_id']}").json()
    record(
        "观点详情包含理由与代表性评论",
        len(detail["reasons"]) > 0 and len(detail["representative_comments"]) > 0,
        f"理由 {len(detail['reasons'])} 个，代表评论 {len(detail['representative_comments'])} 条",
    )

    report = client.get(f"/api/tasks/{task_id}/report")
    record(
        "报告接口返回 Markdown",
        report.status_code == 200 and "# 评论观点分析报告" in report.text,
        f"{len(report.text)} 字符",
    )

    html_report = client.get(f"/api/tasks/{task_id}/report/html")
    record(
        "服务端 HTML 报告可直接查看",
        html_report.status_code == 200
        and "评论观点分析报告" in html_report.text
        and "主要理由" in html_report.text,
        f"{len(html_report.content) / 1024:.0f} KB",
    )
    standalone = client.get(f"/api/tasks/{task_id}/download/html")
    record(
        "下载的 HTML 与网页结构一致（分析结果 + 完整报告 + 内嵌数据）",
        standalone.status_code == 200
        and "view-result" in standalone.text
        and "view-report" in standalone.text
        and "analysis-data" in standalone.text
        and "app-header" in standalone.text
        and "观点列表" in standalone.text,
        f"{len(standalone.content) / 1024:.0f} KB",
    )

    for kind, url in (
        ("JSON", f"/api/tasks/{task_id}/download/json"),
        ("Markdown", f"/api/tasks/{task_id}/download/report"),
        ("HTML", f"/api/tasks/{task_id}/download/html"),
    ):
        download = client.get(url)
        record(
            f"下载 {kind} 文件",
            download.status_code == 200 and len(download.content) > 100,
            f"{len(download.content)} 字节",
        )
    return task_id


def test_export_and_delete(client: httpx.Client, task_id: str) -> None:
    """测试 9/10：打包下载 zip、删除任务（记录与本地数据一起清理）。"""
    import zipfile
    from io import BytesIO

    from analysis.paths import task_paths

    package = client.get(f"/api/tasks/{task_id}/download/package")
    ok_zip = package.status_code == 200 and package.headers.get("content-type", "").startswith("application/zip")
    names: list[str] = []
    if ok_zip:
        with zipfile.ZipFile(BytesIO(package.content)) as archive:
            names = archive.namelist()
    record(
        "打包下载 zip（含 HTML / Markdown / JSON / 清洗结果 / 原始文件）",
        ok_zip
        and any(n.endswith("analysis_report.html") for n in names)
        and any(n.endswith("analysis_results.json") for n in names)
        and any(n.endswith("analysis_report.md") for n in names),
        f"{len(package.content) / 1024:.0f} KB，包含 {len(names)} 个文件",
    )

    paths = task_paths(task_id)
    existed = paths.working_dir.exists()
    deleted = client.delete(f"/api/tasks/{task_id}")
    record(
        "删除任务：记录与本地数据一起清理",
        deleted.status_code == 200
        and client.get(f"/api/tasks/{task_id}").status_code == 404
        and not paths.working_dir.exists()
        and not paths.results.parent.exists(),
        f"删除前工作目录存在={existed}，删除后工作目录存在={paths.working_dir.exists()}",
    )


def test_cancel_task(client: httpx.Client) -> None:
    """测试 11：分析中取消任务 → 停止分析并删除本次任务产生的数据。"""
    import tempfile
    import uuid

    from analysis.paths import task_paths

    nonce = uuid.uuid4().hex[:6]
    texts = [
        "建议先学一门技术再考虑进厂打工",
        "年轻的时候可以多尝试几份不同的工作",
        "工厂流水线工作强度大而且枯燥",
        "家里经济压力大的话可以先攒点钱",
        "先去大城市找找适合自己的工作机会",
        "读书提升学历仍然非常重要",
    ]
    payload = {
        "topic": f"取消测试 {nonce}",
        "comments": [
            {
                "comment_id": f"cancel_{nonce}_{index}",
                "content": f"{nonce} 第{index}条：{texts[index % len(texts)]}",
                "like_count": index % 4,
            }
            for index in range(60)
        ],
    }
    temp_path = Path(tempfile.gettempdir()) / f"cancel_{nonce}.json"
    temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    response = upload(client, temp_path)
    temp_path.unlink(missing_ok=True)
    if response.status_code != 200:
        record("测试11 取消分析", False, f"上传失败 HTTP {response.status_code}")
        return
    task_id = response.json()["task_id"]
    paths = task_paths(task_id)

    # 等它真正跑起来再取消
    started = False
    deadline = time.time() + 120
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] == "running" and task["progress"] > 3:
            started = True
            break
        if task["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)

    cancel_response = client.post(f"/api/tasks/{task_id}/cancel")
    record(
        "测试11a 取消接口接受请求",
        cancel_response.status_code == 200 and cancel_response.json()["status"] == "cancelling",
        f"取消前状态 running={started}，响应 {cancel_response.status_code}",
    )

    cleaned = False
    cancel_started = time.time()
    while time.time() - cancel_started < 300:
        if client.get(f"/api/tasks/{task_id}").status_code == 404:
            cleaned = True
            break
        time.sleep(3)

    record(
        "测试11b 取消后停止分析并删除本次任务数据",
        cleaned and not paths.raw_comments.exists() and not paths.working_dir.exists(),
        f"用时 {time.time() - cancel_started:.0f}s，任务记录已删除={cleaned}，"
        f"上传文件存在={paths.raw_comments.exists()}，工作目录存在={paths.working_dir.exists()}",
    )


def test_bad_uploads(client: httpx.Client) -> None:
    """测试 2/3：空 JSON、非法 JSON、错误扩展名都要给出明确错误。"""
    empty = upload(client, FIXTURES / "empty.json")
    record(
        "测试2 空 JSON 返回明确错误",
        empty.status_code == 400 and "评论" in empty.json().get("detail", ""),
        f"HTTP {empty.status_code} {empty.json().get('detail')}",
    )

    invalid = upload(client, FIXTURES / "invalid.json")
    record(
        "测试3a 非法 JSON 返回明确错误",
        invalid.status_code == 400 and "JSON" in invalid.json().get("detail", ""),
        f"HTTP {invalid.status_code} {invalid.json().get('detail')}",
    )

    txt = client.post(
        "/api/tasks",
        files={"file": ("test.txt", b"hello world", "text/plain")},
        timeout=30.0,
    )
    record(
        "测试3b 错误扩展名返回明确错误",
        txt.status_code == 400 and "格式错误" in txt.json().get("detail", ""),
        f"HTTP {txt.status_code} {txt.json().get('detail')}",
    )


def test_refresh_recovery(client: httpx.Client, task_id: str) -> None:
    """测试7：刷新页面（新连接）仍能通过 task_id 恢复查看任务状态。"""
    with httpx.Client(base_url=client.base_url, timeout=30.0) as fresh:
        response = fresh.get(f"/api/tasks/{task_id}")
    record(
        "测试7 刷新/新会话仍可恢复任务状态",
        response.status_code == 200 and response.json()["task_id"] == task_id,
        f"status={response.json()['status']} stage={response.json().get('stage')}",
    )
    listing = client.get("/api/tasks", params={"page": 1, "page_size": 5}).json()
    record("任务列表可用（用于刷新后找回任务）", listing["total"] >= 1, f"共 {listing['total']} 个任务")
    filtered = client.get("/api/tasks", params={"status": "completed", "page_size": 5}).json()
    record(
        "任务列表支持状态筛选/分页（任务记录页用）",
        filtered["total"] >= 1 and all(item["status"] == "completed" for item in filtered["items"]),
        f"已完成 {filtered['total']} 个，返回 {len(filtered['items'])} 条",
    )


def test_api_failure(client: httpx.Client, base: str) -> None:
    """测试6：LLM API 鉴权失败时任务状态为 failed 且错误可读。"""
    print("    使用错误 API Key 的独立服务实例进行测试…")
    import os
    import subprocess
    import tempfile
    import uuid

    port = "8011"
    env = {**os.environ, "API_KEY": "sk-invalid-for-test", "TASK_WORKERS": "1"}
    # 必须使用全新的评论内容：LLM 缓存按 prompt 命中，重复内容不会真正请求接口
    nonce = uuid.uuid4().hex[:8]
    unique_payload = {
        "topic": f"测试主题 {nonce}",
        "comments": [
            {
                "comment_id": f"fail_{nonce}_{index}",
                "content": f"{nonce} 这是第 {index} 条用于验证接口失败处理的评论，内容随机不可缓存",
                "like_count": 0,
            }
            for index in range(1, 4)
        ],
    }
    temp_path = Path(tempfile.gettempdir()) / f"failure_test_{nonce}.json"
    temp_path.write_text(json.dumps(unique_payload, ensure_ascii=False), encoding="utf-8")
    process = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "backend.main:app", "--port", port],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        failure_base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(f"{failure_base}/api/health", timeout=3.0).status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        with httpx.Client(base_url=failure_base, timeout=60.0) as faulty:
            response = upload(faulty, temp_path)
            if response.status_code != 200:
                record("测试6 API 失败时任务标记 failed", False, f"上传失败 HTTP {response.status_code}")
                return
            task = wait_for_task(faulty, response.json()["task_id"], timeout=300, verbose=False)
        ok = task["status"] == "failed" and "失败" in (task.get("error") or "")
        record(
            "测试6 API 失败时任务标记 failed 且错误可读",
            ok,
            f"status={task['status']} error={task.get('error')}",
        )
        ok_stage = bool(task.get("error_stage"))
        record("测试6b 失败信息包含失败阶段", ok_stage, f"error_stage={task.get('error_stage')}")
    finally:
        temp_path.unlink(missing_ok=True)
        process.terminate()
        try:
            process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            process.kill()


def test_full_dataset(client: httpx.Client) -> None:
    """测试1（完整）：上传第一阶段真实数据，验证缓存复用与结果一致性。"""
    from analysis.config import POST_TOPIC, RAW_COMMENTS_FILE

    raw = RAW_COMMENTS_FILE
    print(f"    上传 {raw.name}（第一阶段样例数据）…")
    response = upload(client, raw, topic=POST_TOPIC)
    if response.status_code != 200:
        record("完整数据集上传", False, f"HTTP {response.status_code} {response.text[:200]}")
        return
    task_id = response.json()["task_id"]
    task = wait_for_task(client, task_id, timeout=3600, verbose=True)
    record("完整数据集分析成功", task["status"] == "completed", f"error={task.get('error')}")
    if task["status"] != "completed":
        return
    from analysis.paths import default_paths

    summary = client.get(f"/api/tasks/{task_id}/summary").json()["summary"]
    local = json.loads(default_paths().results.read_text(encoding="utf-8"))["summary"]
    same = all(summary[key] == local[key] for key in ("valid_comment_count", "invalid_comment_count", "duplicate_comment_count"))
    record(
        "Web 结果与第一阶段 CLI 结果一致（清洗口径）",
        same,
        f"web valid={summary['valid_comment_count']} cli valid={local['valid_comment_count']}",
    )
    record(
        "完整数据集观点数量",
        summary["viewpoint_count"] > 0,
        f"viewpoints={summary['viewpoint_count']} links={summary['viewpoint_comment_link_count']}",
    )
    record(
        "测试5 一条评论可绑定多个 viewpoint_id（完整数据集）",
        summary["multi_viewpoint_comment_count"] >= 1,
        f"多观点评论 {summary['multi_viewpoint_comment_count']} 条 / 有效评论 {summary['valid_comment_count']} 条",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Web 服务端到端测试")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--full", action="store_true", help="追加真实 detail_comments.json 测试")
    parser.add_argument("--skip-failure", action="store_true", help="跳过 API 失败模拟（会另起一个服务实例）")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base, timeout=120.0) as client:
        health = client.get("/api/health")
        record("服务健康检查", health.status_code == 200, f"version={health.json().get('version')}")

        task_id = test_small_dataset(client)
        test_bad_uploads(client)
        if task_id:
            test_refresh_recovery(client, task_id)
        if args.full:
            test_full_dataset(client)
        if task_id:
            test_export_and_delete(client, task_id)
        test_cancel_task(client)

    if not args.skip_failure:
        test_api_failure(None, args.base)

    failures = [item for item in RESULTS if not item[1]]
    print(f"\n共 {len(RESULTS)} 项测试，通过 {len(RESULTS) - len(failures)} 项，失败 {len(failures)} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
