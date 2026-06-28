import asyncio
import json
import logging
from typing import Optional

from .ai_config import configured_providers, provider_info
from .ai_providers import achat_json
from .ai_schemas import AIEnhancedReport, AIApproach, AIQueryResult
from .prompts import QUERY_OPTIMIZER_SYSTEM, REPORT_ENHANCER_SYSTEM, APPROACH_SYSTEM

logger = logging.getLogger("agency_finder.ai_pipeline")


def _best_provider() -> Optional[str]:
    providers = configured_providers()
    return providers[0] if providers else None


def _best_model(provider: str) -> str:
    models = provider_info(provider).get("fallback_models", [])
    return models[0] if models else ""


async def aoptimize_search_query(name: str, *,
                                 provider: Optional[str] = None,
                                 model: Optional[str] = None,
                                 timeout: int = 30) -> list[str]:
    provider = provider or _best_provider()
    if not provider:
        return []
    model = model or _best_model(provider)

    try:
        result = await achat_json(
            provider, model,
            messages=[{"role": "user", "content": f"Agency name: {name}"}],
            schema=AIQueryResult,
            system=QUERY_OPTIMIZER_SYSTEM,
            timeout=timeout,
        )
        queries = result.queries[:8]
        logger.info(f"AI optimized queries for '{name}': {queries}")
        return queries
    except Exception as e:
        logger.warning(f"AI query optimization failed for '{name}': {e}")
        return []


async def aenhance_report(result: dict, *,
                          provider: Optional[str] = None,
                          model: Optional[str] = None,
                          timeout: int = 60) -> Optional[AIEnhancedReport]:
    provider = provider or _best_provider()
    if not provider:
        return None
    model = model or _best_model(provider)

    data_str = json.dumps(result, indent=2, ensure_ascii=False)
    if len(data_str) > 25000:
        data_str = data_str[:25000] + "... [truncated]"

    try:
        report = await achat_json(
            provider, model,
            messages=[{"role": "user", "content": f"Raw extraction data:\n\n{data_str}"}],
            schema=AIEnhancedReport,
            system=REPORT_ENHANCER_SYSTEM,
            timeout=timeout,
        )
        logger.info("AI report enhancement completed")
        return report
    except Exception as e:
        logger.warning(f"AI report enhancement failed: {e}")
        return None


async def acommercial_approach(result: dict, *,
                               provider: Optional[str] = None,
                               model: Optional[str] = None,
                               timeout: int = 60) -> Optional[AIApproach]:
    provider = provider or _best_provider()
    if not provider:
        return None
    model = model or _best_model(provider)

    data_str = json.dumps(result, indent=2, ensure_ascii=False)
    if len(data_str) > 25000:
        data_str = data_str[:25000] + "... [truncated]"

    try:
        approach = await achat_json(
            provider, model,
            messages=[{"role": "user", "content": f"Agency data:\n\n{data_str}"}],
            schema=AIApproach,
            system=APPROACH_SYSTEM,
            timeout=timeout,
        )
        logger.info("AI commercial approach completed")
        return approach
    except Exception as e:
        logger.warning(f"AI commercial approach failed: {e}")
        return None


async def aprocess_full(result: dict, *,
                        provider: Optional[str] = None,
                        model: Optional[str] = None,
                        timeout: int = 60) -> dict:
    provider = provider or _best_provider()
    if not provider:
        return result
    model = model or _best_model(provider)

    enhance_task = asyncio.create_task(aenhance_report(result, provider=provider, model=model))
    approach_task = asyncio.create_task(acommercial_approach(result, provider=provider, model=model))

    done, pending = await asyncio.wait(
        [enhance_task, approach_task],
        timeout=timeout,
        return_when=asyncio.ALL_COMPLETED,
    )

    for t in pending:
        t.cancel()

    for task in done:
        if task is enhance_task:
            try:
                report = task.result()
                if report is not None:
                    result["ai_enhanced"] = report.model_dump()
            except Exception as e:
                logger.warning(f"AI enhancement result error: {e}")
        elif task is approach_task:
            try:
                approach = task.result()
                if approach is not None:
                    result["ai_approach"] = approach.model_dump()
            except Exception as e:
                logger.warning(f"AI approach result error: {e}")

    return result
