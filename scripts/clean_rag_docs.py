"""
Clean LlamaParse markdown cache files:
- Remove page headers/footers mixed into content
- Remove navigation menus, timestamps, URLs, page numbers
- Remove copyright/trademark boilerplate
"""
from __future__ import annotations

import re
from pathlib import Path

DATA_DIR = Path("/Users/xyg/就业/AI/Agent/i-member/data/rag_docs")

# --- Patterns to remove (whole line match or line-level removal) ---

# Timestamp breadcrumbs: "2026/5/1 22:23 ASICS" etc.
RE_TIMESTAMP = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}\s*.*$")

# Bare URLs
RE_URL_ONLY = re.compile(r"^https?://\S+$")

# Page numbers: "1/13", "10/10" etc.
RE_PAGE_NUM = re.compile(r"^\d{1,3}/\d{1,3}$")

# Copyright
RE_COPYRIGHT = re.compile(r"^©\s*\d{4}\s+(ASICS|Onitsuka Tiger)\..*$")

# Trademark boilerplate
RE_TRADEMARK = re.compile(
    r"^The stripe design featured on the sides of ASICS shoes.*$"
)

# ICP / 公安备案
RE_ICP = re.compile(r"^(沪ICP备|沪公安网备)[\d号].*$")

# Company footer lines (exact match after strip)
COMPANY_FOOTER = {
    "亚瑟士(中国)商贸有限公司",
    "中国大陆",
    "ONITSUKA TIGER IS A REGISTERED TRADEMARK OF ASICS CORPORATION",
}

# Navigation menu lines (only in leading blocks, but safe to remove standalone)
NAV_ITEMS = {
    "商城", "品牌", "精选系列", "所有商品", "男士", "女士", "儿童",
    "网站支持", "客户服务", "关于鬼塚虎", "尺码对照表", "关于订单",
    "隐私政策", "店铺查找", "联系我们", "关于Onitsuka Tiger",
    "Cookie政策", "销售条款", "会员规则", "社交媒体账号", "在线客服",
    "电子营业执照", "SIZES",
}

# Leading garbage: before the first markdown heading
# These lines often contain nav menus repeated
RE_LEADING_NAV = re.compile(
    r"^(商城|品牌|精选系列|所有商品|男士|女士|儿童)\s*$"
)


def is_noise(line: str) -> bool:
    """Return True if this line is noise to remove."""
    s = line.strip()
    if not s:
        return False  # keep blank lines for now, collapse later

    if RE_TIMESTAMP.match(s):
        return True
    if RE_URL_ONLY.match(s):
        return True
    if RE_PAGE_NUM.match(s):
        return True
    if RE_COPYRIGHT.match(s):
        return True
    if RE_TRADEMARK.match(s):
        return True
    if RE_ICP.match(s):
        return True
    if s in COMPANY_FOOTER:
        return True
    if s in NAV_ITEMS:
        return True
    # "# 在线客服" style standalone nav headings
    if re.match(r"^#\s*(在线客服|电子\s*营业执照|Onitsuka Tiger)$", s):
        return True

    return False


def _find_content_start(lines: list[str]) -> int:
    """Find the first line that looks like real content (a markdown heading)."""
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("# ") and not is_noise(line):
            return i
    return 0


def _find_content_end(lines: list[str]) -> int:
    """Find the last line that looks like real content."""
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if not s:
            continue
        if not is_noise(lines[i]):
            return i + 1  # exclusive
    return len(lines)


def collapse_blanks(lines: list[str]) -> list[str]:
    """Collapse 3+ consecutive blank lines to 2."""
    result = []
    blank_count = 0
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)
    return result


def clean_file(path: Path) -> int:
    """Clean a single markdown file. Returns number of lines removed."""
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    # 1. Find content boundaries
    start = _find_content_start(lines)
    end = _find_content_end(lines)

    # 2. Extract content range + filter noise lines within
    body = []
    removed = 0
    for i in range(start, end):
        if is_noise(lines[i].rstrip("\n")):
            removed += 1
            continue
        body.append(lines[i])

    # 3. Also count removed head/tail lines
    removed += start + (len(lines) - end)

    # 4. Collapse multiple blank lines
    cleaned = collapse_blanks(body)

    # 5. Strip leading/trailing blank lines
    while cleaned and cleaned[0].strip() == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1].strip() == "":
        cleaned.pop(-1)

    # Add trailing newline
    result = "".join(cleaned).rstrip() + "\n"

    path.write_text(result, encoding="utf-8")
    return removed


def main():
    md_files = sorted(DATA_DIR.glob("*.md"))
    print(f"Processing {len(md_files)} files...\n")

    for path in md_files:
        removed = clean_file(path)
        new_size = path.stat().st_size
        print(f"  {path.name}: removed ~{removed} lines, new size={new_size}B")


if __name__ == "__main__":
    main()
