from __future__ import annotations

from typing import Any, Dict, List

_MAX_PRODUCTS_FOR_LLM = 8
_MAX_COLORS_FOR_LLM = 8
_MAX_SIZES_FOR_LLM = 32
_ONITSUKA_CN_PRODUCT_DETAIL_URL = "https://www.onitsukatiger.com/cn/zh-cn/detail/{product_id}-{color_id}"

_product_default_color_cache: Dict[int, int] = {}


def cache_default_color(product_id: Any, color_id: Any) -> None:
    try:
        pid = int(product_id)
        cid = int(color_id)
    except Exception:
        return
    _product_default_color_cache[pid] = cid


def get_cached_default_color(product_id: Any) -> int | None:
    try:
        return _product_default_color_cache.get(int(product_id))
    except Exception:
        return None


def build_official_product_url(product_id: Any, color_id: Any) -> str:
    try:
        pid = int(product_id)
        cid = int(color_id)
    except Exception:
        return ""
    return _ONITSUKA_CN_PRODUCT_DETAIL_URL.format(product_id=pid, color_id=cid)


def summarize_product(item: Dict[str, Any]) -> Dict[str, Any]:
    product_id = int(item.get("id") or 0)
    default_color_id = item.get("color")
    if default_color_id:
        cache_default_color(product_id, default_color_id)

    default_color_name = ""
    default_color_stock = None
    for color in list(item.get("colors") or []):
        if str(color.get("id")) == str(default_color_id):
            default_color_name = str(color.get("name") or "").strip()
            default_color_stock = color.get("stock")
            break

    return {
        "product_id": product_id,
        "name": item.get("name"),
        "category": (item.get("category") or {}).get("name"),
        "gender": (item.get("gender") or {}).get("name"),
        "price": item.get("price") or item.get("minPrice"),
        "original_price": item.get("original_price"),
        "default_color_id": default_color_id,
        "default_color_name": default_color_name,
        "image": item.get("cover"),
        "hover_image": item.get("hoverCover"),
        "product_labels": item.get("product_label") or [],
        "official_url": build_official_product_url(product_id, default_color_id),
        "stock": default_color_stock,
        "in_stock": bool((default_color_stock or 0) > 0),
    }


def visible_products(products: List[Dict[str, Any]], *, limit: int = _MAX_PRODUCTS_FOR_LLM) -> List[Dict[str, Any]]:
    visible: List[Dict[str, Any]] = []
    for item in list(products or [])[:limit]:
        visible.append(
            {
                "product_id": item.get("product_id"),
                "name": item.get("name"),
                "category": item.get("category"),
                "gender": item.get("gender"),
                "price": item.get("price"),
                "original_price": item.get("original_price"),
                "default_color_id": item.get("default_color_id"),
                "default_color_name": item.get("default_color_name"),
                "color_id": item.get("default_color_id"),
                "color_name": item.get("default_color_name"),
                "image": item.get("image"),
                "official_url": item.get("official_url"),
                "product_labels": list(item.get("product_labels") or [])[:3],
                "in_stock": item.get("in_stock"),
            }
        )
    return visible


def trim_colors(colors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    visible: List[Dict[str, Any]] = []
    for item in list(colors or [])[:_MAX_COLORS_FOR_LLM]:
        visible.append(
            {
                "color_id": item.get("color_id"),
                "name": item.get("name"),
                "in_stock": item.get("in_stock"),
            }
        )
    return visible


def trim_sizes(sizes: List[Dict[str, Any]], *, in_stock_only: bool = False) -> List[str]:
    source = list(sizes or [])
    if in_stock_only:
        source = [item for item in source if item.get("in_stock")]
    visible: List[str] = []
    seen: set[str] = set()
    for item in source[:_MAX_SIZES_FOR_LLM]:
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        visible.append(name)
    return visible


def adapt_product_detail(data: Dict[str, Any]) -> Dict[str, Any]:
    cache_default_color(data.get("id"), data.get("color"))

    colors = []
    for item in list(data.get("colors") or []):
        if not isinstance(item, dict):
            continue
        colors.append(
            {
                "color_id": item.get("id"),
                "name": item.get("name"),
                "value": item.get("value"),
                "cover": item.get("cover"),
                "stock": item.get("stock"),
                "price": item.get("price"),
                "in_stock": bool((item.get("stock") or 0) > 0),
            }
        )

    sizes = []
    for item in list(data.get("sizes") or []):
        if not isinstance(item, dict):
            continue
        sizes.append(
            {
                "sku_id": item.get("id"),
                "name": item.get("name"),
                "price": item.get("price"),
                "stock": item.get("stock"),
                "in_stock": bool((item.get("stock") or 0) > 0),
            }
        )

    current_color_id = data.get("color")
    current_color_name = ""
    current_color_cover = ""
    for color in colors:
        if str(color.get("color_id")) == str(current_color_id):
            current_color_name = str(color.get("name") or "").strip()
            current_color_cover = str(color.get("cover") or "").strip()
            break
    sizes_in_stock = trim_sizes(sizes, in_stock_only=True)
    adapted = {
        "product_id": data.get("id"),
        "name": data.get("name"),
        "category": (data.get("category") or {}).get("name"),
        "gender": (data.get("gender") or {}).get("name"),
        "price": data.get("price"),
        "original_price": data.get("original_price"),
        "color_id": current_color_id,
        "color_name": current_color_name,
        "image": current_color_cover,
        "in_stock": bool(sizes_in_stock),
        "official_url": build_official_product_url(data.get("id"), current_color_id),
        "product_labels": list(data.get("product_label") or [])[:3],
        "available_colors": trim_colors(colors),
        "available_sizes": sizes_in_stock,
    }
    adapted["result_summary"] = (
        f"{adapted.get('name')}，当前价 {adapted.get('price')} 元，"
        f"可选颜色 {len(colors)} 个，有货尺码 {len(adapted['available_sizes'])} 个。"
    )
    return adapted
