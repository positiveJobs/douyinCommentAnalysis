# data/ 目录说明

这里是**本地数据目录**，整体已被 `.gitignore` 忽略（只保留本说明文件），不会进入版本库。

```text
data/
├── samples/     手工放置待分析的评论 JSON（CLI 默认读 samples/detail_comments.json）
├── cli/         CLI（run_pipeline.py）的中间结果与产物
├── uploads/     Web 上传的原始文件，按任务命名 <task_id>.json
├── working/     Web 任务的中间结果（清洗、语义分析、聚类、归并、抽样…）
├── results/     Web 任务最终结果 analysis_results.json
├── reports/     Web 任务最终报告 analysis_report.md
└── tasks.db     Web 任务状态（SQLite）
```

- 目录会在需要时自动创建，删除后重新运行即可再生（有 `cache/` 缓存时几乎不额外消耗 API）。
- 想分析自己的数据：把 JSON 放到 `data/samples/`，或在 Web 页面直接上传。
- 确实需要把某份数据纳入版本管理时，可以强制添加：`git add -f data/samples/xxx.json`。
