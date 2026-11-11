"""评论观点语义分析引擎（可复用核心）。

设计原则：LLM 负责理解语言，Python 负责数据处理与统计。

对外统一入口：

    from analysis import CommentAnalysisPipeline

    pipeline = CommentAnalysisPipeline(input_file="comments.json", task_id="t1")
    result = pipeline.run()

CLI、FastAPI 与未来的 Skill 都调用同一套 Pipeline，分析逻辑不重复实现。
"""

from .paths import Paths, default_paths, task_paths
from .pipeline import CommentAnalysisPipeline, PipelineError, run_all

__all__ = [
    "__version__",
    "CommentAnalysisPipeline",
    "PipelineError",
    "Paths",
    "default_paths",
    "task_paths",
    "run_all",
]

__version__ = "2.0.0"
