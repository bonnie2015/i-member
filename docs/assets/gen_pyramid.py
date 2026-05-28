#!/usr/bin/env python3
"""直接生成图2：评测分层金字塔 SVG"""
import os, xml.etree.ElementTree as ET

OUT = os.path.join(os.path.dirname(__file__), "eval-pyramid.svg")

# 深橙红色 — 仅用于 capability / regression / CI 三个标签文字
C_LIFECYCLE_TEXT = "#c2410c"

lines = []
def add(s):
    lines.append(s)

add('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 550" width="800" height="550">')
add('  <style>')
add('    text { font-family: "Helvetica Neue", Helvetica, Arial, "PingFang SC", "Microsoft YaHei", "Microsoft JhengHei", "SimHei", sans-serif; }')
add('    .title { font-size: 22px; font-weight: 700; fill: #111827; }')
add('    .section-title { font-size: 18px; font-weight: 700; }')
add('    .section-detail { font-size: 12px; font-weight: 500; fill: #6b7280; }')
add('    .section-badge { font-size: 10px; font-weight: 700; letter-spacing: 0.08em; }')
add('    .lifecycle-title { font-size: 14px; font-weight: 700; fill: #111827; }')
add('    .lifecycle-node-label { font-size: 11px; font-weight: 700; }')
add('    .lifecycle-node-sub { font-size: 9px; fill: #9ca3af; }')
add('    .note-text { font-size: 10px; font-weight: 400; fill: #9ca3af; }')
add('    .note-highlight { font-size: 10px; font-weight: 600; fill: #c2410c; }')
add('  </style>')
add('  <defs>')
add('    <marker id="arrow-lifecycle" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">')
add('      <polygon points="0 0, 8 3, 0 6" fill="#dc2626"/>')
add('    </marker>')
add('    <marker id="arrow-gray" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">')
add('      <polygon points="0 0, 8 3, 0 6" fill="#6b7280"/>')
add('    </marker>')
add('    <filter id="shadow" x="-10%" y="-10%" width="130%" height="130%">')
add('      <feDropShadow dx="0" dy="2" stdDeviation="4" flood-color="#0f172a" flood-opacity="0.10"/>')
add('    </filter>')
add('  </defs>')

# Background
add('  <rect width="800" height="550" fill="#ffffff"/>')

# Title
add('  <text x="400" y="38" text-anchor="middle" class="title">i-member 评测分层体系</text>')

# ── Pyramid ──
# Apex: (350, 95), Base: from (40, 480) to (660, 480)
# Left edge slope: (40-350)/385 = -0.805, Right: (660-350)/385 = 0.805
# At y=225: L=245.3, R=454.7
# At y=350: L=144.7, R=555.3

# Layer 3: 单元测试 (bottom)
add('  <polygon points="144.7,350 555.3,350 660,480 40,480" fill="#f0fdf4" stroke="#16a34a" stroke-width="1.8" filter="url(#shadow)"/>')
add('  <text x="350" y="388" text-anchor="middle" class="section-title" fill="#16a34a">单元测试</text>')
add('  <text x="350" y="410" text-anchor="middle" class="section-detail">纯函数 · CodeGrader</text>')
add('  <text x="350" y="428" text-anchor="middle" class="section-detail">秒级执行 · CI 每次提交</text>')
add('  <rect x="320" y="442" width="60" height="18" rx="4" fill="#dcfce7" stroke="#86efac" stroke-width="1"/>')
add('  <text x="350" y="454" text-anchor="middle" class="section-badge" fill="#16a34a">FOUNDATION</text>')

# Layer 2: Agent单测 (middle)
add('  <polygon points="245.3,225 454.7,225 555.3,350 144.7,350" fill="#eff6ff" stroke="#2563eb" stroke-width="1.8" filter="url(#shadow)"/>')
add('  <text x="350" y="260" text-anchor="middle" class="section-title" fill="#2563eb">Agent 单测</text>')
add('  <text x="350" y="282" text-anchor="middle" class="section-detail">单 Agent 行为 · CodeGrader 断言</text>')
add('  <text x="350" y="300" text-anchor="middle" class="section-detail">state 状态 · 工具链 · 状态转换</text>')
add('  <rect x="310" y="316" width="80" height="18" rx="4" fill="#dbeafe" stroke="#93c5fd" stroke-width="1"/>')
add('  <text x="350" y="328" text-anchor="middle" class="section-badge" fill="#2563eb">INTEGRATION</text>')

# Layer 1: E2E (top)
add('  <polygon points="350,95 454.7,225 245.3,225" fill="#faf5ff" stroke="#9333ea" stroke-width="1.8" filter="url(#shadow)"/>')
add('  <text x="350" y="145" text-anchor="middle" class="section-title" fill="#9333ea">E2E 全链路</text>')
add('  <text x="350" y="167" text-anchor="middle" class="section-detail">用户模拟器驱动</text>')
add('  <text x="350" y="185" text-anchor="middle" class="section-detail">ModelGrader 三维度 + Human 抽检</text>')
add('  <rect x="315" y="198" width="70" height="18" rx="4" fill="#ede9fe" stroke="#c4b5fd" stroke-width="1"/>')
add('  <text x="350" y="210" text-anchor="middle" class="section-badge" fill="#9333ea">END-TO-END</text>')

# ── Right side: 测试用例生命周期 ──
add('  <rect x="675" y="130" width="110" height="320" rx="10" fill="#fef2f2" stroke="#fecaca" stroke-width="1.5"/>')

add('  <text x="730" y="158" text-anchor="middle" class="lifecycle-title">测试用例</text>')
add('  <text x="730" y="176" text-anchor="middle" class="lifecycle-title">生命周期</text>')

# Vertical dashed line
add('  <line x1="730" y1="195" x2="730" y2="389" stroke="#e5e7eb" stroke-width="1.5" stroke-dasharray="4,4"/>')

# 三个节点统一深橙红边框+标签
add(f'  <rect x="692" y="210" width="76" height="34" rx="8" fill="#ffffff" stroke="{C_LIFECYCLE_TEXT}" stroke-width="1.8"/>')
add(f'  <text x="730" y="226" text-anchor="middle" class="lifecycle-node-label" fill="{C_LIFECYCLE_TEXT}">capability</text>')
add('  <text x="730" y="238" text-anchor="middle" class="lifecycle-node-sub">能力测试</text>')

# Arrow: capability → regression
add('  <line x1="730" y1="248" x2="730" y2="270" stroke="#dc2626" stroke-width="1.5" marker-end="url(#arrow-lifecycle)"/>')

# Transition: ≥ 90% pass³ (only between capability → regression)
add('  <rect x="695" y="258" width="70" height="14" rx="4" fill="#ffffff" opacity="0.9"/>')
add(f'  <text x="730" y="269" text-anchor="middle" style="font-size:9px;font-weight:600;fill:{C_LIFECYCLE_TEXT};">≥ 90% pass³</text>')

add(f'  <rect x="690" y="285" width="80" height="34" rx="8" fill="#ffffff" stroke="{C_LIFECYCLE_TEXT}" stroke-width="1.8"/>')
add(f'  <text x="730" y="301" text-anchor="middle" class="lifecycle-node-label" fill="{C_LIFECYCLE_TEXT}">regression</text>')
add('  <text x="730" y="313" text-anchor="middle" class="lifecycle-node-sub">回归守护</text>')

# Arrow: regression → CI
add('  <line x1="730" y1="323" x2="730" y2="345" stroke="#dc2626" stroke-width="1.5" marker-end="url(#arrow-lifecycle)"/>')

add(f'  <rect x="692" y="355" width="76" height="34" rx="8" fill="#ffffff" stroke="{C_LIFECYCLE_TEXT}" stroke-width="1.8"/>')
add(f'  <text x="730" y="371" text-anchor="middle" class="lifecycle-node-label" fill="{C_LIFECYCLE_TEXT}">CI</text>')
add('  <text x="730" y="383" text-anchor="middle" class="lifecycle-node-sub">安全兜底</text>')

# ── Connection lines: pyramid → lifecycle ──
add('  <line x1="455" y1="160" x2="675" y2="227" stroke="#d1d5db" stroke-width="1" stroke-dasharray="3,3"/>')
add('  <line x1="455" y1="288" x2="675" y2="302" stroke="#d1d5db" stroke-width="1" stroke-dasharray="3,3"/>')
add('  <line x1="555" y1="415" x2="675" y2="372" stroke="#d1d5db" stroke-width="1" stroke-dasharray="3,3"/>')

# ── Bottom: CI 策略说明 ──
add('  <rect x="40" y="500" width="600" height="32" rx="6" fill="#f9fafb" stroke="#e5e7eb" stroke-width="1"/>')
add('  <text x="340" y="520" text-anchor="middle" class="note-text">'
     'CI 策略：push 跑单元测试 + Agent 单测 ｜ PR 按 diff 跑对应层测试 ｜ merge main 跑全量 regression'
     '</text>')

add('</svg>')

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"✓ SVG written: {OUT}")

ET.parse(OUT)
print("✓ XML valid")
