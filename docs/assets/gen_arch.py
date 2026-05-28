#!/usr/bin/env python3
"""架构图 v7 — User/Router 居中，箭头正交"""
import os

OUT = os.path.join(os.path.dirname(__file__), "architecture-overview.svg")
B, P, R, G = "#2563eb", "#9333ea", "#dc2626", "#6b7280"
BG = "#f8fafc"

def gen():
    L = []
    def _(s): L.append(s)

    _('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1060 680" width="1060" height="680">')
    _('<style>text{font-family:"Helvetica Neue",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;}</style>')
    _('<defs>')
    for mid, c in [("ab",B),("ap",P),("ar",R),("ag",G)]:
        _(f'<marker id="{mid}" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="{c}"/></marker>')
    _('<filter id="sh"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.08"/></filter>')
    _('</defs>')
    _('<rect width="1060" height="680" fill="#ffffff"/>')
    _('<text x="530" y="34" text-anchor="middle" font-size="21" font-weight="700" fill="#111827">i-member 系统架构</text>')
    _('<text x="530" y="54" text-anchor="middle" font-size="12" fill="#9ca3af">LangGraph 多 Agent 智能导购 · 3 子图协作</text>')

    # ── helpers ──
    def box(x, y, w, h, title, sub="", color="", bold=False):
        c = color or "#d1d5db"
        sw = 2.2 if bold else 1.8
        _(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#ffffff" stroke="{c}" stroke-width="{sw}" filter="url(#sh)"/>')
        if sub:
            _(f'<text x="{x+w/2}" y="{y+h/2-5}" text-anchor="middle" font-size="14" font-weight="700" fill="#111827">{title}</text>')
            _(f'<text x="{x+w/2}" y="{y+h/2+14}" text-anchor="middle" font-size="11" fill="#6b7280">{sub}</text>')
        else:
            _(f'<text x="{x+w/2}" y="{y+h/2+4}" text-anchor="middle" font-size="14" font-weight="700" fill="#111827">{title}</text>')

    def small(x, y, w, h, title, tag):
        _(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="#ffffff" stroke="#d1d5db" stroke-width="1.4" filter="url(#sh)"/>')
        _(f'<text x="{x+w/2}" y="{y+14}" text-anchor="middle" font-size="10" font-weight="700" fill="#9ca3af">{tag}</text>')
        _(f'<text x="{x+w/2}" y="{y+h-10}" text-anchor="middle" font-size="13" font-weight="700" fill="#111827">{title}</text>')

    def line(x1, y1, x2, y2, color=B, mid="ab", dash="", lw=1.5):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        _(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{lw}"{d} marker-end="url(#{mid})"/>')

    def path(points, color=B, mid="ab", dash="", lw=1.5):
        """points: list of (x,y) tuples"""
        d = f"M {points[0][0]},{points[0][1]}"
        for px, py in points[1:]:
            d += f" L {px},{py}"
        ds = f' stroke-dasharray="{dash}"' if dash else ""
        _(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{lw}"{ds} marker-end="url(#{mid})"/>')

    def label(x, y, text, c="#6b7280"):
        w = len(text)*8.5 + 14
        _(f'<rect x="{x-w/2}" y="{y-8}" width="{w}" height="16" rx="4" fill="#ffffff" opacity="0.92"/>')
        _(f'<text x="{x}" y="{y+3}" text-anchor="middle" font-size="10" font-weight="600" fill="{c}">{text}</text>')

    def user_icon(cx, cy):
        _(f'<circle cx="{cx}" cy="{cy}" r="15" fill="#dbeafe" stroke="#93c5fd" stroke-width="1.5"/>')
        _(f'<circle cx="{cx}" cy="{cy-5}" r="4" fill="#93c5fd"/>')
        _(f'<path d="M {cx-10},{cy+11} Q {cx},{cy+2} {cx+10},{cy+11}" fill="none" stroke="#93c5fd" stroke-width="1.5"/>')

    # ══════════════════════════════════════
    # LAYER 1: User + Router
    # ══════════════════════════════════════
    cx = 180

    user_icon(cx, 92)
    _('<text x="180" y="122" text-anchor="middle" font-size="13" font-weight="600" fill="#111827">User</text>')

    box(90, 140, 180, 58, "Router", "意图分类：ticket / qa / recommend", color=B, bold=True)

    # User → Router
    line(cx, 107, cx, 140, B, "ab", lw=2)

    # ══════════════════════════════════════
    # Router → 三子图 (正交 L 形箭头)
    # ══════════════════════════════════════
    # Router 底部: (180, 198)

    # Router → 三子图: 斜线分叉
    _(f'<line x1="180" y1="198" x2="260" y2="252" stroke="{B}" stroke-width="2" marker-end="url(#ab)"/>')
    label(210, 220, "qa")
    _(f'<line x1="180" y1="198" x2="530" y2="252" stroke="{B}" stroke-width="2" marker-end="url(#ab)"/>')
    label(345, 220, "ticket")
    _(f'<line x1="180" y1="198" x2="800" y2="252" stroke="{B}" stroke-width="2" marker-end="url(#ab)"/>')
    label(640, 220, "recommend")

    # ══════════════════════════════════════
    # LAYER 2: Subgraphs
    # ══════════════════════════════════════

    # ── QA 子图 (左) ──
    _(f'<rect x="140" y="260" width="240" height="265" rx="8" fill="{BG}" stroke="#e2e8f0" stroke-width="1"/>')
    _(f'<text x="260" y="282" text-anchor="middle" font-size="11" font-weight="700" fill="#94a3b8">QA 子图</text>')
    box(165, 330, 190, 70, "qa_node", "RAG 检索 + 生成回答")
    _('<text x="260" y="420" text-anchor="middle" font-size="10" fill="#9ca3af">Token 超限 → 对话压缩</text>')

    # ── Ticket 子图 (中) ──
    _(f'<rect x="400" y="260" width="260" height="265" rx="8" fill="{BG}" stroke="#e2e8f0" stroke-width="1"/>')
    _(f'<text x="530" y="282" text-anchor="middle" font-size="11" font-weight="700" fill="#94a3b8">TICKET 子图</text>')

    nodes = [
        (420, 298, 220, 36, "guard_node",    "选技能"),
        (420, 340, 220, 36, "plan_node",     "生成步骤"),
        (420, 382, 220, 42, "executor_node", "手动 ReAct · 调工具"),
        (420, 430, 220, 36, "reflect_node",  "判断进度"),
        (420, 472, 220, 36, "finalize_node", "最终回复 + 工单卡"),
    ]
    for x, y, w, h, title, tag in nodes:
        small(x, y, w, h, title, tag)

    # 内部流（细灰线）
    for i in range(4):
        line(530, nodes[i][1]+nodes[i][3], 530, nodes[i+1][1], color=G, mid="ag", lw=1.2)

    # Replan (左侧折线, reflect → plan, 离开边缘避免看不清)
    path([(420,448), (404,448), (404,358), (420,358)], R, "ar", "6,4", lw=1.3)
    _('<text x="397" y="404" text-anchor="middle" font-size="9" font-weight="600" fill="#dc2626">replan</text>')

    # ── Recommend 子图 (右) ──
    _(f'<rect x="680" y="260" width="240" height="265" rx="8" fill="{BG}" stroke="#e2e8f0" stroke-width="1"/>')
    _(f'<text x="800" y="282" text-anchor="middle" font-size="11" font-weight="700" fill="#94a3b8">RECOMMEND 子图</text>')
    small(705, 315, 190, 40, "recommend_guard_node", "压缩上下文 · 判断完成")
    box(705, 395, 190, 55, "recommend_node", "ReAct 搜索 + 展示卡片")
    _('<text x="800" y="468" text-anchor="middle" font-size="10" fill="#9ca3af">轮次上限 5 · 多轮 guard</text>')
    line(800, 355, 800, 395, G, "ag", lw=1.2)

    # ══════════════════════════════════════
    # LAYER 3: Post-Processing (居中)
    # ══════════════════════════════════════
    box(200, 555, 660, 52, "Post-Processing", "summary_agent + user_facts_agent（后台异步）", color=G, bold=True)

    # 子图 → Post-Processing: 三条直下
    line(260, 525, 260, 551, B, "ab", lw=2)
    line(530,  525, 530,  551, B, "ab", lw=2)
    line(800, 525, 800, 551, B, "ab", lw=2)

    # ══════════════════════════════════════
    # FOOTER
    # ══════════════════════════════════════
    _('<rect x="140" y="630" width="780" height="36" rx="8" fill="#f9fafb" stroke="#e2e8f0" stroke-width="1"/>')
    _('<text x="530" y="652" text-anchor="middle" font-size="11" fill="#6b7280">关键机制：技能渐进披露 · try_process 审计链 · 中断恢复 v3 · 服务切换（interaction card）</text>')

    _('</svg>')
    return "\n".join(L)

svg = gen()
with open(OUT, "w") as f:
    f.write(svg)
print(f"✓ {OUT}")

import xml.etree.ElementTree as ET
ET.parse(OUT)
print("✓ XML valid")
