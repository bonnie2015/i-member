from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, TypedDict, get_args

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.tools.business.execution_context import (
    get_business_execution_context,
    push_ticket_interaction_source,
)
from app.tools.business.onitsuka_adapter import (
    adapt_product_detail,
    get_cached_default_color,
    summarize_product,
    visible_products,
)
from app.tools.business.onitsuka_client import (
    get_product_detail as _client_get_product_detail,
    list_products as _client_list_products,
    search_products as _client_search_products,
)
from app.tools.business.onitsuka_semantics import (
    BRAND_QUERY_ALIASES,
    GENDER_FILTER_IDS,
    IGNORED_BRAND_KEYWORDS,
    PRICE_FILTER_VALUES,
    SHOE_QUERY_ALIASES,
    SIZE_FILTER_IDS,
    build_where_payload,
    normalize_sort,
)
from app.config.logging import get_logger

logger = get_logger("onitsuka_tools")

_RESULT_LIMIT = 4
_DEFAULT_PAGE = 1
_ALL_PRODUCTS_CATEGORY_ID = 572

GenderFilterValue = Literal["", "女性", "女子", "男性", "男子", "中性", "儿童"]
ColorFilterValue = Literal[
    "",
    "紫色",
    "米色",
    "棕色",
    "橙色",
    "黄色",
    "绿色",
    "蓝色",
    "白色",
    "红色",
    "银色",
    "灰色",
    "粉色",
    "黑色",
    "PALE BLUE/WHITE",
]
PriceFilterValue = Literal[
    "", "850-1150", "1150-1450", "1450-1750", "2050-2350", "3850-4150"
]
CategoryValue = Literal[
    "",
    "鞋",
    "德训鞋",
    "休闲鞋",
    "运动鞋",
    "板鞋",
    "凉鞋",
    "厚底鞋",
    "皮鞋",
    "乐福鞋",
    "靴",
    "高跟鞋",
    "帽子",
    "T恤",
    "外套",
    "衬衫",
    "夹克",
    "卫衣",
    "针织衫",
    "背心",
    "马甲",
    "连衣裙",
    "大衣",
    "长裤",
    "短裤",
    "半裙",
    "短裙",
    "开衫",
    "POLO衫",
    "羽绒服",
    "包",
    "背包",
    "单肩包",
    "托特包",
    "双肩包",
    "斜挎包",
    "邮差包",
    "手提包",
    "腰包",
    "帽",
    "棒球帽",
    "渔夫帽",
    "针织帽",
    "鸭舌帽",
]


class SearchPath(TypedDict):
    stage: str
    query: str


class CursorPayload(TypedDict):
    query: str
    filters: Dict[str, Any]
    sort: str
    page: int
    page_size: int
    source_query: str
    source_filters: Dict[str, Any]


class AliasIndex(TypedDict):
    exact: Dict[str, str]
    contains: Dict[str, str]
    pattern: re.Pattern[str] | None


class SearchByIntentInput(BaseModel):
    gender: GenderFilterValue = Field(
        default="",
        description=(
            "会影响搜索结果的过滤条件。仅在用户本轮明确指定人群时填写；未明确则空字符串。"
            "说明：女性/男性两个选项多用于偏时尚潮流都市类，女子/男子选项偏休闲经典运动类，但不绝对，可能需要尝试。"
            "用户指定男子/女子时，不要忽略中性商品；如用户未排斥中性，可尝试 gender=中性。"
            "建议先不加性别搜索，找到方向后再根据搜索结果判断是否需要加性别过滤"
        ),
    )
    size: List[str] = Field(
        default_factory=list,
        description=(
            "会影响搜索结果的过滤条件。仅在用户本轮明确提供尺码时填写；未明确则空数组。"
            "鞋码填写欧码数字，如 36、37.5、42；童鞋可填写 110、120、130、140；服装填写 XS、S、M、L、XL、2XL。"
            "不要从历史偏好、用户画像或商品详情中擅自补充。搜索不理想时，可尝试放宽尺码过滤，但回复中要说明。"
        ),
    )
    color: ColorFilterValue = Field(
        default="",
        description=(
            "会影响搜索结果的过滤条件。仅在用户本轮明确颜色时填写；未明确则空字符串。"
            "不要从历史偏好、用户画像或商品详情中擅自补充。"
        ),
    )
    price: PriceFilterValue = Field(
        default="",
        description=(
            "会影响搜索结果的过滤条件。用户对价格、预算敏感时灵活运用；"
            "也可以用 sort=price:asc 从低价优先检索。不要从长期偏好或用户画像中擅自补充预算；"
            "若价格过滤导致结果过少，可放宽价格过滤但回复中要说明。"
        ),
    )
    category: CategoryValue = Field(
        default="",
        description=(
            "商品品类，与 keyword 二选一，不能同时填写。"
            "鞋类品类：鞋、德训鞋、休闲鞋、板鞋、厚底鞋、皮鞋、乐福鞋、靴等。"
            "非鞋品类：T恤、衬衫、夹克、裤装、裙装、配饰等。"
            "品类不明确时留空。"
        ),
    )
    keyword: str = Field(
        default="",
        description=(
            "搜索关键词，与 category 二选一，不能同时填写。仅在以下候选词中选 1 个："
            "1. 鞋品系列：MEXICO 66、MEXICO 66 NM、MEXICO 66 SD、MEXICO 66 SLIP-ON、MEXICO 66 PARATY、"
            "MEXICO 66 TGRS、SLIP-ON、TOKUTEN、SERRANO、GSM、EDR 78、DELECITY、LAWNSHIP、ULTIMATE 81、"
            "TIGER CORSAIR、BIG LOGO TRAINER、TIGRUN、TIGTRAIL、TRASPIKE、DENTIGRE、DELEGATION、"
            "ADMIX TRAINER、MOAL 77、COLORADO EIGHTY-FIVE、COLESNE、OHBORI、REBILAC、TSUNAHIKI、"
            "TIGER MOC、TIGER RODEANO、WINTER HEAVEN、SIDE GORE BOOT、LACE-UP、BLUCHER、TIGER LOAFER。"
            "2. 鞋类描述：经典、复古、一脚蹬、无鞋带、芭蕾风、德训、赛车、薄底、平板鞋、系带。"
            "3. 品牌语义：印花、图案、刺绣、贴布绣、水洗、扎染、牛仔、针织、皮质、条纹、格纹、花卉、涂鸦、拼接、撞色、铆钉、亮片、漆皮、蕾丝、"
            "经典、纯色、简约、复古、法式、度假、飞行员、工装、优雅、个性、休闲、时尚、宽松、修身、阔腿、直筒、短袖、长袖、无袖、"
            "连帽、套头、拉链、开衫、圆领、吊带、双排扣、防风、铺棉、厚底。"
        ),
    )
    sort: Literal["", "new", "sales", "price:asc", "price:desc"] = Field(
        default="",
        description=(
            "排序，在用户有新品、热销、价格要求时，必须积极运用排序工具帮助提升搜索准确度。用户说新款、最近上新、有没有新品、看看最新款时填 new；"
            "用户说热销、热门、卖得好时填 sales；用户要求便宜优先/从低到高填 price:asc；"
            "用户说贵了点/还是贵/便宜点/预算有限/性价比高，也应优先考虑 price:asc；"
            "用户要求贵一点/从高到低填 price:desc；没有排序意图才留空。sort 本身就是有效搜索参数，用户只问新品/最新时可以只传 sort=new。"
        ),
    )
    cursor: str = Field(
        default="",
        description="翻页游标。对同一批搜索结果翻页，原样传上一次工具返回的 next_cursor；cursor 内的 query 表示真实命中的查询。",
    )


class GetOnitsukaProductDetailInput(BaseModel):
    product_id: int = Field(
        description="商品 ID。通常来自 search_products 的返回结果。"
    )
    color_id: int = Field(
        description="颜色 ID，必填。通常来自 search_products 的返回结果。product_id 和 color_id 必须同时提供，不允许留空。",
    )


def _dedupe_texts(values: Any) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    if values is None:
        raw_values: List[Any] = []
    elif isinstance(values, (list, tuple, set)):
        raw_values = list(values)
    else:
        raw_values = [values]
    for item in raw_values:
        text = str(item or "").strip()
        if not text:
            continue
        key = _normalized_key(text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _normalized_key(value: str) -> str:
    return " ".join(
        str(value or "").strip().lower().replace("™", "").replace("-", " ").split()
    )


def _is_brand_keyword(value: str) -> bool:
    return _normalized_key(value) in IGNORED_BRAND_KEYWORDS


def _build_alias_index(
    alias_map: Dict[str, List[str]], *, namespace: str
) -> AliasIndex:
    exact: Dict[str, str] = {}
    contains: Dict[str, str] = {}
    for canonical, aliases in alias_map.items():
        canonical_key = _normalized_key(canonical)
        for alias in aliases:
            alias_key = _normalized_key(alias)
            if not alias_key or alias_key == canonical_key:
                continue
            existing = exact.get(alias_key)
            if existing:
                if existing != canonical:
                    logger.warning(
                        "[onitsuka_alias] namespace=%s duplicate_alias=%s kept=%s ignored=%s",
                        namespace,
                        alias,
                        existing,
                        canonical,
                    )
                continue
            exact[alias_key] = canonical
            if len(alias_key) > 1:
                contains[alias_key] = canonical

    contains_keys = sorted(contains, key=lambda value: (-len(value), value))
    pattern = (
        re.compile("|".join(re.escape(value) for value in contains_keys))
        if contains_keys
        else None
    )
    return {"exact": exact, "contains": contains, "pattern": pattern}


_SHOE_QUERY_ALIAS_INDEX = _build_alias_index(SHOE_QUERY_ALIASES, namespace="shoe_query")
_BRAND_QUERY_ALIAS_INDEX = _build_alias_index(
    BRAND_QUERY_ALIASES, namespace="brand_query"
)


def _lookup_alias(term: str, alias_index: AliasIndex) -> str:
    term_key = _normalized_key(term)
    if not term_key:
        return ""

    exact = alias_index["exact"].get(term_key)
    if exact:
        return exact

    pattern = alias_index.get("pattern")
    if pattern is None:
        return ""
    matched = pattern.search(term_key)
    if not matched:
        return ""
    return alias_index["contains"].get(matched.group(0), "")


def _lookup_canonical(term: str, canonical_values: List[str]) -> str:
    term_key = _normalized_key(term)
    if not term_key:
        return ""

    matches: List[tuple[int, int, str]] = []
    for canonical in canonical_values:
        canonical_key = _normalized_key(canonical)
        if not canonical_key:
            continue
        if term_key == canonical_key:
            return canonical
        position = term_key.find(canonical_key)
        if position >= 0:
            matches.append((position, -len(canonical_key), canonical))
    if not matches:
        return ""
    return sorted(matches)[0][2]


def _first_text(value: Any) -> str:
    values = _dedupe_texts(value)
    return values[0] if values else ""


def _map_category(category: Any) -> str:
    terms = _dedupe_texts(category)
    if not terms:
        return ""
    return terms[0]


def _is_shoe_context(category: str) -> bool:
    text = str(category or "").strip()
    return not text or "鞋" == text


def _is_shoe_category(category: str) -> bool:
    text = str(category or "").strip()
    return bool(
        text and text in get_args(CategoryValue) and ("鞋" in text or "靴" in text)
    )


def _is_specific_shoe_category(category: str) -> bool:
    text = str(category or "").strip()
    return bool(text and text != "鞋" and _is_shoe_category(text))


def _resolve_keyword(keyword: Any, *, shoe_context: bool) -> tuple[List[str], str]:
    terms = _dedupe_texts(keyword)
    if not terms or _is_brand_keyword(terms[0]):
        return [], ""

    term = terms[0]
    if shoe_context:
        canonical_shoe_query = _lookup_canonical(term, list(SHOE_QUERY_ALIASES.keys()))
        if canonical_shoe_query:
            return [canonical_shoe_query], "shoe"
        shoe_query = _lookup_alias(term, _SHOE_QUERY_ALIAS_INDEX)
        if shoe_query:
            return [shoe_query], "shoe"

    canonical_brand_query = _lookup_canonical(term, list(BRAND_QUERY_ALIASES.keys()))
    if canonical_brand_query:
        return [canonical_brand_query], "brand"
    brand_query = _lookup_alias(term, _BRAND_QUERY_ALIAS_INDEX)
    if brand_query:
        return [brand_query], "brand"

    return [term], "raw"


def _map_keyword(keyword: Any, *, shoe_context: bool) -> List[str]:
    resolved_keywords, _ = _resolve_keyword(keyword, shoe_context=shoe_context)
    return resolved_keywords


def _join_query(*groups: List[str]) -> str:
    return " ".join(_dedupe_texts([item for group in groups for item in group]))


def _dedupe_paths(candidates: List[tuple[str, str]]) -> List[SearchPath]:
    paths: List[SearchPath] = []
    seen: set[str] = set()
    for stage, query in candidates:
        text = str(query or "").strip()
        key = f"{stage}:{_normalized_key(text)}"
        if not stage or key in seen:
            continue
        seen.add(key)
        paths.append({"stage": stage, "query": text})
    return paths


def _build_query_plan(
    *,
    category: Any,
    keyword: Any,
    include_filter_only: bool = False,
) -> tuple[List[SearchPath], List[SearchPath]]:
    category_value = _map_category(category)
    selected_keywords, keyword_kind = _resolve_keyword(
        keyword, shoe_context=_is_shoe_context(category_value)
    )
    raw_keywords = _dedupe_texts(keyword)
    if raw_keywords and _is_brand_keyword(raw_keywords[0]):
        raw_keywords = []
    if not selected_keywords:
        selected_keywords = raw_keywords
    keyword_stage = f"{keyword_kind}_keyword" if keyword_kind else "raw_keyword"

    primary_candidates: List[tuple[str, str]] = []
    fallback_candidates: List[tuple[str, str]] = []
    if category_value:
        if selected_keywords:
            if _is_specific_shoe_category(category_value):
                primary_candidates.append(("category", category_value))
            elif category_value == "鞋" or keyword_kind == "shoe":
                primary_candidates.append(
                    (keyword_stage, _join_query(selected_keywords))
                )
                if include_filter_only:
                    fallback_candidates.append(("filter_only", ""))
            else:
                primary_stage = f"category_{keyword_stage}"
                primary_candidates.append(
                    (primary_stage, _join_query([category_value], selected_keywords))
                )
                fallback_candidates.append(
                    ("fallback_keyword", _join_query(selected_keywords))
                )
        else:
            primary_candidates.append(("category", category_value))
    elif selected_keywords:
        primary_candidates.append((keyword_stage, _join_query(selected_keywords)))
        if include_filter_only:
            fallback_candidates.append(("filter_only", ""))
    elif include_filter_only:
        primary_candidates.append(("filter_only", ""))

    return _dedupe_paths(primary_candidates), _dedupe_paths(fallback_candidates[:1])


def _build_where_variants(
    *,
    gender: Any,
    size: Any,
    color: Any,
    price: Any,
) -> List[Dict[str, str]]:
    gender_values = [
        GENDER_FILTER_IDS[value]
        for value in _dedupe_texts(gender)
        if value in GENDER_FILTER_IDS
    ]
    size_values = [
        SIZE_FILTER_IDS[value]
        for value in _dedupe_texts(size)
        if value in SIZE_FILTER_IDS
    ]
    price_values = [
        value for value in _dedupe_texts(price) if value in PRICE_FILTER_VALUES
    ]
    filters = {
        "gender": gender_values[:1],
        "size": [",".join(size_values)] if size_values else [],
        "color": _dedupe_texts(color)[:1],
        "price": price_values[:1],
    }
    if not any(filters.values()):
        return [{}]

    sizes = filters["size"] or [""]

    variants: List[Dict[str, str]] = []
    for size_value in sizes:
        variants.append(
            build_where_payload(
                gender=(filters["gender"] or [""])[0],
                size=size_value,
                color=(filters["color"] or [""])[0],
                price_range=(filters["price"] or [""])[0],
            )
        )
    return variants or [{}]


def _encode_cursor(
    *,
    query: str,
    where: Dict[str, Any],
    sort: str,
    page: int,
    page_size: int,
    source_query: str = "",
    source_filters: Dict[str, Any] | None = None,
) -> str:
    payload = {
        "query": query,
        "filters": dict(where or {}),
        "sort": sort,
        "page": int(page),
        "page_size": int(page_size),
    }
    if str(source_query or "").strip():
        payload["source_query"] = str(source_query or "").strip()
    if source_filters:
        payload["source_filters"] = dict(source_filters or {})
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _decode_cursor(cursor: str) -> CursorPayload | None:
    token = str(cursor or "").strip()
    if not token:
        return None
    try:
        payload = json.loads(token)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    query = str(payload.get("query") or "").strip()
    raw_filters = payload.get("filters", {})
    where = raw_filters if isinstance(raw_filters, dict) else {}
    page = int(payload.get("page", 0) or 0)
    page_size = int(payload.get("page_size", 0) or 0)
    if page < 1 or page_size < 1:
        return None
    return {
        "query": query,
        "filters": {
            str(key): value for key, value in where.items() if str(value or "").strip()
        },
        "sort": normalize_sort(str(payload.get("sort") or ""), default=""),
        "page": page,
        "page_size": page_size,
        "source_query": str(payload.get("source_query") or query).strip(),
        "source_filters": {
            str(key): value
            for key, value in (
                payload.get("source_filters")
                if isinstance(payload.get("source_filters"), dict)
                else where
            ).items()
            if str(value or "").strip()
        },
    }


def _build_next_cursor(
    *,
    query: str,
    where: Dict[str, Any],
    sort: str,
    page: int,
    page_size: int,
    total_found: int,
    source_query: str = "",
    source_filters: Dict[str, Any] | None = None,
) -> str:
    if total_found <= page * page_size:
        return ""
    return _encode_cursor(
        query=query,
        where=where,
        sort=sort,
        page=page,
        page_size=page_size,
        source_query=source_query,
        source_filters=source_filters,
    )


def _source_query_for_path(query: str, requested_params: Dict[str, Any]) -> str:
    actual_query = str(query or "").strip()
    requested_category = str(requested_params.get("category") or "").strip()
    requested_keyword = str(requested_params.get("keyword") or "").strip()
    if not actual_query:
        return ""

    source_parts: List[str] = []
    if requested_category and requested_category in actual_query:
        source_parts.append(requested_category)
    if requested_keyword and (
        not requested_category or actual_query != requested_category
    ):
        source_parts.append(requested_keyword)
    return _join_query(source_parts) or actual_query


def _source_filters_for_where(
    where: Dict[str, Any], requested_params: Dict[str, Any]
) -> Dict[str, Any]:
    source_filters: Dict[str, Any] = {}
    for key, value in dict(where or {}).items():
        if not str(value or "").strip():
            continue
        source_filters[str(key)] = requested_params.get(str(key)) or value
    return source_filters


def _build_product_cursor(
    *,
    source_query: str,
    source_filters: Dict[str, Any],
    sort: str,
    page: int,
    page_size: int,
    next_cursor: str,
) -> Dict[str, Any]:
    return {
        "source_query": str(source_query or "").strip(),
        "source_filters": {
            str(key): value
            for key, value in dict(source_filters or {}).items()
            if str(value or "").strip()
        },
        "sort": str(sort or "").strip(),
        "page": page,
        "page_size": page_size,
        "has_next": bool(next_cursor),
        "next_cursor": str(next_cursor or "").strip(),
    }


def _build_product_search_result(
    *,
    products: List[Dict[str, Any]],
    total_found: int,
) -> Dict[str, Any]:
    display_products = visible_products(products, limit=_RESULT_LIMIT)
    return {
        "products": display_products,
        "total_found": total_found,
    }


def _full_where_payload(where: Dict[str, Any]) -> Dict[str, str]:
    payload = {key: "" for key in ("gender", "size", "color", "price")}
    for key, value in dict(where or {}).items():
        if key in payload:
            payload[key] = str(value or "").strip()
    return payload


async def _run_list_products(
    *,
    where: Dict[str, str],
    sort: str,
    requested_params: Dict[str, Any] | None = None,
    page: int = _DEFAULT_PAGE,
    page_size: int = _RESULT_LIMIT,
) -> tuple[List[Dict[str, Any]], SearchPath, Dict[str, str], int, Dict[str, Any]]:
    context = get_business_execution_context()
    normalized_requested_params = (
        requested_params if isinstance(requested_params, dict) else {}
    )
    list_sort = normalize_sort(sort, default="new") or "new"
    list_where = _full_where_payload(where)
    result = await _client_list_products(
        category_id=_ALL_PRODUCTS_CATEGORY_ID,
        where=list_where,
        sort=list_sort,
        limit=page_size,
        page=page,
    )
    if "error" in result:
        logger.warning(
            "[onitsuka_search] thread_id=%s user_id=%s stage=list_all sort=%s filters=%s page=%s error=%s",
            context["thread_id"],
            context["user_id"],
            list_sort,
            list_where,
            page,
            result.get("error_code") or result.get("error"),
        )
        return [], {"stage": "list_all", "query": ""}, dict(where or {}), 0, {}

    data = result.get("data") or {}
    products = [
        summarize_product(item)
        for item in list(data.get("list") or [])
        if isinstance(item, dict)
    ]
    total = int(data.get("total", 0) or 0)
    source_filters = _source_filters_for_where(where, normalized_requested_params)
    cursor = _build_product_cursor(
        source_query="全部商品",
        source_filters=source_filters,
        sort=list_sort,
        page=page,
        page_size=page_size,
        next_cursor="",
    )
    logger.info(
        "[onitsuka_search] thread_id=%s user_id=%s stage=list_all total=%s products=%s filters=%s sort=%s page=%s",
        context["thread_id"],
        context["user_id"],
        total,
        len(products),
        list_where,
        list_sort,
        page,
    )
    return (
        products,
        {"stage": "list_all", "query": ""},
        dict(where or {}),
        total,
        cursor,
    )


async def _run_search_paths(
    *,
    paths: List[SearchPath],
    where_variants: List[Dict[str, str]],
    sort: str,
    requested_params: Dict[str, Any] | None = None,
    page: int = _DEFAULT_PAGE,
    page_size: int = _RESULT_LIMIT,
) -> tuple[
    List[Dict[str, Any]], SearchPath | None, Dict[str, str], int, Dict[str, Any]
]:
    context = get_business_execution_context()
    normalized_requested_params = (
        requested_params if isinstance(requested_params, dict) else {}
    )
    for path in paths:
        for where in where_variants:
            if path.get("stage") == "filter_only":
                (
                    products,
                    selected_path,
                    selected_where,
                    total,
                    cursor,
                ) = await _run_list_products(
                    where=where,
                    sort=sort,
                    requested_params=normalized_requested_params,
                    page=page,
                    page_size=_RESULT_LIMIT,
                )
                if products:
                    return products, selected_path, selected_where, total, cursor
                continue
            result = await _client_search_products(
                keyword=path["query"],
                where=where,
                sort=sort,
                limit=page_size,
                page=page,
            )
            if "error" in result:
                logger.warning(
                    "[onitsuka_search] thread_id=%s user_id=%s stage=%s query=%s filters=%s page=%s error=%s",
                    context["thread_id"],
                    context["user_id"],
                    path.get("stage"),
                    path["query"],
                    where,
                    page,
                    result.get("error_code") or result.get("error"),
                )
                continue
            data = result.get("data") or {}
            products = [
                summarize_product(item)
                for item in list(data.get("list") or [])
                if isinstance(item, dict)
            ]
            total = int(data.get("total", 0) or 0)
            source_query = _source_query_for_path(
                path["query"], normalized_requested_params
            )
            source_filters = _source_filters_for_where(
                where, normalized_requested_params
            )
            next_cursor = _build_next_cursor(
                query=path["query"],
                where=where,
                sort=sort,
                page=page,
                page_size=page_size,
                total_found=total,
                source_query=source_query,
                source_filters=source_filters,
            )
            cursor = _build_product_cursor(
                source_query=source_query,
                source_filters=source_filters,
                sort=sort,
                page=page,
                page_size=page_size,
                next_cursor=next_cursor,
            )
            logger.info(
                "[onitsuka_search] thread_id=%s user_id=%s stage=%s query=%s total=%s products=%s filters=%s page=%s",
                context["thread_id"],
                context["user_id"],
                path.get("stage"),
                path.get("query"),
                total,
                len(products),
                where,
                page,
            )
            if products:
                return products, dict(path), dict(where or {}), total, cursor
    return [], None, {}, 0, {}


async def _run_cursor_page(cursor_payload: CursorPayload) -> Dict[str, Any]:
    page = int(cursor_payload["page"]) + 1
    page_size = int(cursor_payload["page_size"])
    query = str(cursor_payload["query"])
    where = dict(cursor_payload["filters"] or {})
    sort = normalize_sort(str(cursor_payload["sort"] or ""), default="")
    result = await _client_search_products(
        keyword=query, where=where, sort=sort, limit=page_size, page=page
    )
    if "error" in result:
        return {
            "products": [],
            "total_found": 0,
            "query": query,
            "error": result.get("error"),
            "error_code": result.get("error_code"),
        }

    data = result.get("data") or {}
    found_products = [
        summarize_product(item)
        for item in list(data.get("list") or [])
        if isinstance(item, dict)
    ]
    total_found = int(data.get("total", 0) or 0)
    source_query = str(cursor_payload.get("source_query") or query).strip()
    source_filters = dict(cursor_payload.get("source_filters") or {})
    next_cursor = _build_next_cursor(
        query=query,
        where=where,
        sort=sort,
        page=page,
        page_size=page_size,
        total_found=total_found,
        source_query=source_query,
        source_filters=source_filters,
    )
    cursor = _build_product_cursor(
        source_query=source_query,
        source_filters=source_filters,
        sort=sort,
        page=page,
        page_size=page_size,
        next_cursor=next_cursor,
    )
    result_payload = _build_product_search_result(
        products=found_products, total_found=total_found
    )
    result_payload["cursor"] = cursor
    if found_products:
        push_ticket_interaction_source({"products": found_products})
    return result_payload


@tool(
    "search_products",
    args_schema=SearchByIntentInput,
    description=(
        "Search Onitsuka Tiger products. "
        "At least one effective parameter is required; any one of category, keyword, gender, size, color, price, sort, or cursor is enough to search. "
        "category 和 keyword 二选一，不能同时传。选 category 时 keyword 必须空，选 keyword 时 category 必须空。"
        "Pass simple filters when needed. "
        "For new arrivals/latest products, passing only sort='new' is valid and should browse latest products. "
        "If the user asks to browse all products or gives no usable search condition, call with empty category/keyword/filters and an explicit sort when needed. "
        "Reuse next_cursor verbatim for pagination. "
        "Empty products list means no matching products; done retry with different filters only when user adds new conditions. "
        "Returns products, total_found, and cursor. "
        "cursor describes the whole returned batch, not a single product."
    ),
)
async def search_products(
    gender: GenderFilterValue = "",
    size: List[str] | None = None,
    color: ColorFilterValue = "",
    price: PriceFilterValue = "",
    category: CategoryValue = "",
    keyword: str = "",
    sort: Literal["", "new", "sales", "price:asc", "price:desc"] = "",
    cursor: str = "",
) -> Dict[str, Any]:
    """Search-only product tool. The tool runs one primary query and at most one deterministic fallback."""
    normalized_sort = normalize_sort(sort, default="")
    context = get_business_execution_context()
    cursor_payload = _decode_cursor(cursor)
    if str(cursor or "").strip():
        if cursor_payload is None:
            logger.warning(
                "[onitsuka_search] thread_id=%s user_id=%s invalid_cursor",
                context["thread_id"],
                context["user_id"],
            )
            return {
                "products": [],
                "total_found": 0,
                "error": "invalid cursor",
                "error_code": "ONITSUKA_INVALID_CURSOR",
            }
        logger.info(
            "[onitsuka_search] thread_id=%s user_id=%s cursor_page query=%s filters=%s page=%s",
            context["thread_id"],
            context["user_id"],
            cursor_payload["query"],
            cursor_payload["filters"],
            int(cursor_payload["page"]) + 1,
        )
        return await _run_cursor_page(cursor_payload)

    requested_params = {
        key: value
        for key, value in {
            "category": _map_category(category),
            "keyword": _first_text(keyword),
            "gender": _first_text(gender),
            "size": ",".join(_dedupe_texts(size)),
            "color": _first_text(color),
            "price": _first_text(price),
        }.items()
        if str(value or "").strip()
    }
    where_variants = _build_where_variants(
        gender=gender, size=size, color=color, price=price
    )
    filters_available = where_variants != [{}]
    primary_paths, fallback_paths = _build_query_plan(
        category=category, keyword=keyword, include_filter_only=filters_available
    )
    if not primary_paths and not fallback_paths:
        primary_paths = [{"stage": "filter_only", "query": ""}]
    logger.info(
        "[onitsuka_search] thread_id=%s user_id=%s input category=%s keyword=%s filters=%s primary_paths=%s fallback_paths=%s",
        context["thread_id"],
        context["user_id"],
        _map_category(category),
        _dedupe_texts(keyword),
        where_variants,
        primary_paths,
        fallback_paths,
    )

    (
        found_products,
        selected_path,
        selected_where,
        total_found,
        result_cursor,
    ) = await _run_search_paths(
        paths=primary_paths,
        where_variants=where_variants,
        sort=normalized_sort,
        requested_params=requested_params,
    )
    if not found_products and fallback_paths:
        logger.info(
            "[onitsuka_search] thread_id=%s user_id=%s deterministic_fallback paths=%s",
            context["thread_id"],
            context["user_id"],
            fallback_paths,
        )
        (
            found_products,
            selected_path,
            selected_where,
            total_found,
            result_cursor,
        ) = await _run_search_paths(
            paths=fallback_paths,
            where_variants=where_variants,
            sort=normalized_sort,
            requested_params=requested_params,
        )

    result = _build_product_search_result(
        products=found_products, total_found=total_found
    )
    result["cursor"] = result_cursor
    if found_products:
        push_ticket_interaction_source({"products": found_products})
    logger.info(
        "[onitsuka_search] thread_id=%s user_id=%s returned=%s total_found=%s selected_path=%s",
        context["thread_id"],
        context["user_id"],
        len(result["products"]),
        total_found,
        selected_path,
    )
    return result


@tool("get_product_detail", args_schema=GetOnitsukaProductDetailInput)
async def get_product_detail(
    product_id: int, color_id: int
) -> Dict[str, Any]:
    """读取商品详情；用于获取商品的尺码 / 库存 / 可选颜色。"""
    resolved_color_id = color_id or get_cached_default_color(product_id)
    if not resolved_color_id:
        return {
            "error": "缺少 color_id，且当前没有缓存到该商品的默认颜色。请先调用 search_products。",
            "error_code": "ONITSUKA_COLOR_ID_REQUIRED",
            "product_id": product_id,
        }

    result = await _client_get_product_detail(
        product_id=int(product_id), color_id=int(resolved_color_id)
    )
    if "error" in result:
        return result
    adapted = adapt_product_detail(result.get("data") or {})
    push_ticket_interaction_source({"products": [adapted]})
    return adapted
