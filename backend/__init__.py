"""评论观点分析 Web 服务（FastAPI）。

Web 层只负责：接收文件 → 创建任务 → 调用 CommentAnalysisPipeline → 查询状态 → 返回结果 / 下载。
所有分析逻辑都在 analysis 包中，与 CLI 共用同一套代码。
"""

__all__ = ["__version__"]

__version__ = "2.0.0"
