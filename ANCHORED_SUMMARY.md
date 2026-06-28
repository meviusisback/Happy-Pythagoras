# Anchored Summary

## Goal
- Build a production-ready Italian web-agency intelligence app with: multi-provider AI layer (query optimization, report enhancement, outreach), robust search backends, sitemap-based portfolio discovery, and payments-company partnership pitch — all working on Streamlit Cloud even when pydantic is missing.

## Constraints & Preferences
- `openai>=1.30.0`, `anthropic>=0.40.0`, `google-genai>=1.0.0`, `pydantic>=2.0.0`, `h2>=4.0.0`, `fake-useragent>=1.5.0`, `ddgs>=8.1.0` in `requirements.txt`
- 6 providers: openai, openrouter, deepseek, opencodego, claude, gemini
- All AI keys session-scoped (sidebar → `os.environ`, never written to `.env`)
- AI import chain fully optional (pydantic guarded with try/except; app works without it)
- HTTP/2 + full browser headers (14 headers including Sec-Ch-Ua, Sec-Fetch-*) for search requests
- DDG library tries `auto → html → lite` backends sequentially with 0.5s delays
- Bing `&brdr=1&setmkt=it-IT`, 2-attempt desktop→mobile UA retry loop
- DDG Lite/HTML: 2-attempt retry with fresh headers, HTTP 202 no-retry
- Partnership pitch from a payments company (Stripe/Nexi/Satispay/Adyen) seeking partnership
- Sender company name configurable via sidebar + `SENDER_COMPANY` env var
- `approach_tone` renamed to `partnership_angle` (defaults to `"partnership"`)
- `partnership_models` field: free text with 5 examples (referral, technology_integration, white_label, joint_gtm, embedded_payments)
- Sitemap portfolio: augments existing portfolio extraction, up to 40 pages, fetches client titles, 9 TLDs (`it, com, eu, net, io, shop, store, biz, co`), runs automatically when URL found
- **Search collects ALL backends** via `asyncio.gather` (15s timeout), merges and deduplicates. No more racing.
- **Domain guessing verifies relevance**: checks if agency name appears in page title/body before accepting. Returns up to 3 verified hits.
- **Scoring requires name in domain or title**: if neither contains the agency name, score is capped at -10.
- **Winner threshold**: score >= 10 (was >= 0). `website_suspect` causes candidate rejection.
- **VAT bonus**: +25 (was +50), requires name within 500 chars of VAT number. Missing VAT penalty removed (was -10, now 0).
- **`_clean_agency_name` preserves domain-relevant terms** (agenzia, studio, marketing, digitale, comunicazione). Only strips search-only modifiers (city names, partita iva, linkedin, srl, etc.). `_clean_agency_name_stripped()` strips everything.
- **`_generate_name_variants()`** produces multiple cleaned names for domain guessing (e.g., "Studio Web Creativo" → "Studio Web Creativo" + "Web Creativo").
- **`_slugify_name`** strips Italian articles/prepositions (di, del, della, e, ed, the, and) to generate extra slug variants.

## Progress
### Done
- **AI layer committed as `23b0566`** — 6 new files, 41 tests, 149 total
- **Pydantic-optional fix committed** — all AI imports lazy/guarded; sidebar shows install instructions when pydantic missing
- **Tuple unpacking fix committed** — `_cached_models` 7-tuple → 6-tuple (removed `redact_keys`)
- **opencodego fix committed** — correct URL `https://opencode.ai/zen/go/v1`, 20 real models, `supports_models_endpoint: True`, hardcoded URL fallback removed
- **Clean error messages committed** — JSON-shape check before `model_validate_json`, catches `ValidationError` with friendly `AIError` messages, debug logging for raw responses
- **ChatCompletion wrapper extraction committed** — `_extract_from_chat_wrapper()` handles opencodego returning full wrapper JSON as string
- **Payments partnership pitch committed** — `APPROACH_SYSTEM_TEMPLATE` + `_build_approach_prompt(sender_company)`, schema updates (`partnership_angle`, `partnership_models`), sidebar input, UI + Markdown export sections
- **Web search hardening committed** — HTTP/2, fake-useragent, `_browser_headers()`, Bing retry, DDG backend rotation, DDGS `headers` arg removed for older versions, HTTP 202 no-retry
- **Wikipedia + Mojeek backends committed** — 7 backends total, `_clean_agency_name()` helper, improved `_aguess_direct_domains()` (full name, 6 TLDs), 15s timeout
- **Sitemap portfolio finder committed as `db1f40a`** — `afind_portfolio_websites()` + helpers in `scraper.py`, wired into `core.py` and `app.py`. URL normalization, expanded `_IGNORE_DOMAINS`.
- **Smarter website discovery committed as `b073ba9`** — search merges all backends, domain guessing verifies relevance, scoring requires name in domain/title, winner threshold >= 10, VAT bonus +25 with proximity check, `website_suspect` causes rejection, `_clean_agency_name` preserves domain-relevant terms, `_generate_name_variants()` produces multiple variants, `_slugify_name` strips Italian articles

### In Progress
- (none)

### Blocked
- (none)

## Key Decisions
- **Lazy AI imports everywhere** — `core.py` and `app.py` never import AI at module level; prevents Streamlit Cloud crash when pydantic isn't installed
- **`json_object` instead of `json_schema`** for AI structured output — broader provider compatibility (some providers including opencodego don't support `json_schema`)
- **`_extract_from_chat_completion_wrapper()`** — when SDK returns raw string (opencodego pattern), tries to parse as ChatCompletion JSON and extract `choices[0].message.content` before schema validation
- **`supports_models_endpoint` flag respected** in `alist_models()` — gates whether to call `/v1/models` or use fallback list
- **Payments-company partnership framing** — AI approach always positioned as partnership from a payments company, not a generic sales pitch
- **7 search backends in `asearch_query`** — DDG lib, DDG Lite, DDG HTML, Mojeek, Bing, Wikipedia, direct-domain guess. Wikipedia has no anti-bot and is always reachable.
- **DDGS constructor: no `headers=` arg** — older `ddgs` versions on Streamlit Cloud don't support it; defensive `TypeError` fallback retries with no args
- **Sitemap portfolio augments existing extraction** — merges new domains into `portfolio_sites` list and stores rich data in `sitemap_portfolio` field
- **`_clean_agency_name()` preserves case** — strips Italian/English search modifiers case-insensitively but keeps original case of the remaining name
- **Search collects all backends** — `asyncio.gather` with 15s timeout instead of racing. Merges and deduplicates all results. Slower (~4-5s vs ~1-2s) but much more reliable.
- **Domain guessing verifies relevance** — checks if agency name appears in page title or body before accepting a guessed domain. Returns up to 3 verified hits.
- **Scoring requires name in domain or title** — if neither contains the agency name, score is capped at -10. Prevents generic/parked pages from winning.
- **Winner threshold >= 10** — requires at least one positive name-matching signal. Was >= 0.
- **VAT bonus +25 with proximity** — requires agency name within 500 chars of VAT number. Prevents false positives from phone numbers/timestamps.
- **`website_suspect` causes rejection** — candidate is skipped and next one tried, instead of just preventing early break.
- **`_clean_agency_name` preserves domain-relevant terms** — "agenzia", "studio", "marketing", "digitale", "comunicazione" are often part of Italian agency domains. Only search-only modifiers are stripped.
- **`_generate_name_variants()`** — produces multiple cleaned names (full + stripped) so domain guessing tries both variants.
- **`_slugify_name` strips Italian articles** — "di", "del", "della", "e", "ed", "the", "and" removed to generate extra slug variants.

## Next Steps
- Deploy and verify on Streamlit Cloud
- Test with real Italian agency names to validate the improvements

## Critical Context
- **215 tests total, all pass**
- **7 search backends**: DDG lib → DDG Lite → DDG HTML → Mojeek → Bing → Wikipedia → direct-domain guess
- **opencodego SDK returns raw ChatCompletion JSON as string** — `_extract_from_chat_completion_wrapper()` unwraps `choices[0].message.content` before schema validation
- **`_get_ai_modules()` returns 6-tuple**: `(get_registered_providers, provider_info, is_configured, set_api_key, clear_all_api_keys, alist_models)` — all unpacking sites must match
- **`_cached_models` unpacks 6 values** at `app.py:35`
- **`response_format` is `json_object`** (not `json_schema`) for all openai-compat providers
- **`ValidationError` imported from pydantic** in `ai_providers.py` (guarded with fallback to `Exception`)
- **`asearch_query` uses `asyncio.gather` with 15s timeout** — collects all backends, merges results, deduplicates by link URL
- **`_aguess_direct_domains` returns up to 3 verified hits** — checks name in page title/body before accepting
- **`_score_html` gates on name in domain/title** — returns -10 if name not found in either
- **Winner threshold is `score >= 10`** — requires at least one positive name-matching signal
- **VAT bonus is +25 with proximity check** — name must appear within 500 chars of VAT number

## Relevant Files
- `agency_finder/ai_config.py`: 6 providers, opencodego at `https://opencode.ai/zen/go/v1` with 20 models, `SENDER_COMPANY` env
- `agency_finder/ai_providers.py`: `_extract_from_chat_completion_wrapper()`, JSON-shape check, `ValidationError` catch, debug logging, `supports_models_endpoint` gate
- `agency_finder/ai_schemas.py`: `partnership_angle` (was `approach_tone`), `partnership_models: list[str]`, stub with `model_dump()`
- `agency_finder/ai_pipeline.py`: `_build_approach_prompt(sender_company)`, `sender_company` param on `acommercial_approach`/`aprocess_full`
- `agency_finder/prompts.py`: `APPROACH_SYSTEM_TEMPLATE`, `_build_approach_prompt()`, payments-company partnership framing
- `agency_finder/config.py`: `AI_ENABLED`, `AI_PROVIDER`, `AI_MODEL`, `SENDER_COMPANY`
- `agency_finder/core.py`: lazy AI imports, `sender_company=Config.SENDER_COMPANY`, sitemap portfolio integration after crawl, `_score_html` with name gate (score -10 if name not in domain/title, domain bonus +50, reduced generic bonuses), `_avat_bonus` with +25 and proximity check, winner threshold `score >= 10`, `website_suspect` causes rejection, fallback requires name in domain/title
- `agency_finder/search.py`: `_browser_headers()` from utils, HTTP/2, 7 backends, DDG lib tries 3 backends, `_clean_agency_name()` (preserves domain-relevant terms), `_clean_agency_name_stripped()` (strips everything), `_generate_name_variants()` (multiple cleaned names), `_slugify_name()` (strips Italian articles), `_aguess_direct_domains()` (multi-variant slugs, relevance verification, up to 3 hits), `asearch_query()` (collects all backends via `asyncio.gather`, 15s timeout, merges + deduplicates), 15s timeout, HTTP 202 no-retry
- `agency_finder/scraper.py`: `_IGNORE_DOMAINS` (expanded), `_CLIENT_FACING_KEYWORDS`, `_ALLOWED_CLIENT_TLDS`, `_fetch_sitemap_locs()`, `_fetch_sitemap_urls()`, `_extract_external_domains()` (normalized URLs), `_extract_direct_candidate_urls()`, `_fetch_client_name()`, `afind_portfolio_websites()`
- `agency_finder/utils.py`: `_browser_headers()`, `fake-useragent` integration, `_parse_sec_ch_ua()`, `_parse_platform()`, `_referer_for_backend()`
- `app.py`: `_get_ai_modules()` 6-tuple, sender_company sidebar input, partnership models UI, sitemap portfolio section + Markdown export, `redact_keys` imported directly
- `requirements.txt`: `h2>=4.0.0`, `fake-useragent>=1.5.0`, `ddgs>=8.1.0`
- `tests/test_ai_layer.py`: 175 tests
- `tests/test_agency_finder.py`: 215 tests (19 new: name variants, relevance verification, score gating, VAT proximity, search merging)
