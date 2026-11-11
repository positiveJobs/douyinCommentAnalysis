"""缓存目录信息：LLM / Embedding 缓存是跨任务共享的（同一份数据不会重复调用 API）。"""
from __future__ import annotations

from analysis import config as analysis_config


def cache_info() -> dict:
    llm_files = list(analysis_config.LLM_CACHE_DIR.glob("*.json")) if analysis_config.LLM_CACHE_DIR.exists() else []
    embedding_entries = 0
    if analysis_config.EMBEDDING_CACHE_FILE.exists():
        with open(analysis_config.EMBEDDING_CACHE_FILE, "r", encoding="utf-8") as handle:
            embedding_entries = sum(1 for line in handle if line.strip())
    repair_entries = 0
    if analysis_config.REPAIR_STORE_FILE.exists():
        import json

        with open(analysis_config.REPAIR_STORE_FILE, "r", encoding="utf-8") as handle:
            repair_entries = len(json.load(handle))
    extraction_dir = analysis_config.CACHE_DIR / "extraction"
    extraction_entries = 0
    extraction_file = analysis_config.EXTRACTION_CACHE_FILE
    if extraction_file.exists():
        with open(extraction_file, "r", encoding="utf-8") as handle:
            extraction_entries = sum(1 for line in handle if line.strip())
    return {
        "llm_cache_dir": str(analysis_config.LLM_CACHE_DIR),
        "llm_cache_entries": len(llm_files),
        "embedding_cache_file": str(analysis_config.EMBEDDING_CACHE_FILE),
        "embedding_cache_entries": embedding_entries,
        "extraction_cache_entries": extraction_entries,
        "extraction_cache_file": str(extraction_file),
        "repair_cache_entries": repair_entries,
        "embedding_model_dir": str(analysis_config.EMBEDDING_MODEL_DIR),
    }
