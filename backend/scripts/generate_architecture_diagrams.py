"""Script to render ProcessOn-grade State Machine and System Architecture diagrams for FormaAI.

Generates ultra-high-definition PNGs (2x Retina scale) via Playwright Chromium.
Ensures zero overlapping lines, zero overlapping arrows, and clean orthogonal routing.
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STATE_MACHINE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>FormaAI State Machine Lifecycle</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    width: 1440px;
    height: 760px;
    background: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    color: #1e293b;
    padding: 26px 36px;
    overflow: hidden;
    position: relative;
  }
  
  /* Top Banner */
  .header-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 2px solid #e2e8f0;
    padding-bottom: 14px;
    margin-bottom: 22px;
  }
  .title-group { display: flex; align-items: center; gap: 12px; }
  .logo-badge {
    background: #2563eb;
    color: #ffffff;
    font-weight: 800;
    font-size: 13px;
    padding: 5px 12px;
    border-radius: 6px;
    letter-spacing: 0.5px;
  }
  .main-title {
    font-size: 19px;
    font-weight: 700;
    color: #0f172a;
    letter-spacing: -0.3px;
  }
  .sub-title {
    font-size: 12px;
    color: #64748b;
    margin-top: 2px;
  }
  .meta-badges { display: flex; gap: 8px; }
  .badge {
    font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    padding: 4px 10px;
    border-radius: 4px;
    font-weight: 600;
  }
  .badge-blue { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
  .badge-emerald { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
  .badge-slate { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }

  /* 4 Phase Columns Container - ProcessOn Grade Orthogonal Spacing */
  .phases-grid {
    display: grid;
    grid-template-columns: repeat(4, 268px);
    justify-content: space-between; /* Generates ~100px inter-phase routing channels */
    position: relative;
    z-index: 2;
  }

  .phase-col {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 12px;
    padding: 14px 12px;
    box-shadow: 0 4px 6px -1px rgba(0,0,0,0.03), 0 2px 4px -2px rgba(0,0,0,0.03);
    display: flex;
    flex-direction: column;
    position: relative;
  }

  .phase-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid #f1f5f9;
    padding-bottom: 8px;
    margin-bottom: 14px;
  }
  .phase-tag {
    font-size: 11px;
    font-weight: 700;
    color: #334155;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .phase-name {
    font-size: 12px;
    font-weight: 600;
    color: #2563eb;
  }

  /* Stage Card - Strictly Fixed Height for Exact Geometric Symmetry */
  .stage-card {
    background: #ffffff;
    border: 1.5px solid #cbd5e1;
    border-radius: 10px;
    padding: 12px 14px;
    position: relative;
    height: 144px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    box-shadow: 0 2px 4px rgba(0,0,0,0.02);
  }
  .stage-card.primary {
    border-color: #3b82f6;
    background: linear-gradient(180deg, #ffffff 0%, #f8faff 100%);
  }
  .stage-card.accent {
    border-color: #10b981;
    background: linear-gradient(180deg, #ffffff 0%, #f0fdf4 100%);
  }
  .stage-card.row2 {
    margin-top: 68px; /* Clean 68px vertical channel for intra-phase connectors */
  }

  .stage-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
  }
  .stage-seq {
    font-size: 10px;
    font-family: ui-monospace, monospace;
    font-weight: 700;
    color: #64748b;
    background: #f1f5f9;
    padding: 2px 6px;
    border-radius: 4px;
  }
  .stage-seq.blue { background: #dbeafe; color: #1e40af; }
  .stage-seq.green { background: #d1fae5; color: #065f46; }

  .stage-name {
    font-size: 14px;
    font-weight: 700;
    color: #0f172a;
    font-family: ui-monospace, monospace;
  }
  .stage-desc {
    font-size: 11.5px;
    font-weight: 600;
    color: #334155;
  }
  
  .spec-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .tag {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 3px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    color: #475569;
  }
  .tag-highlight {
    background: #eff6ff;
    border-color: #bfdbfe;
    color: #1d4ed8;
    font-weight: 600;
  }
  .tag-green {
    background: #ecfdf5;
    border-color: #a7f3d0;
    color: #047857;
    font-weight: 600;
  }

  /* Bottom Security Safeguard Bar - Independent Channel */
  .security-footer {
    margin-top: 72px; /* Generous 72px clearance for I2V redo loop */
    background: #0f172a;
    border-radius: 8px;
    padding: 12px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: #94a3b8;
    font-size: 11px;
    position: relative;
    z-index: 5;
  }
  .guard-item { display: flex; align-items: center; gap: 8px; }
  .guard-dot { width: 6px; height: 6px; border-radius: 50%; background: #38bdf8; }
  .guard-title { color: #f8fafc; font-weight: 600; }

  /* SVG Overlay for Connectors */
  svg.connectors {
    position: absolute;
    top: 0;
    left: 0;
    width: 1440px;
    height: 760px;
    pointer-events: none;
    z-index: 10;
  }
</style>
</head>
<body>

  <!-- Top Title -->
  <div class="header-bar">
    <div class="title-group">
      <div class="logo-badge">FORMA.AI</div>
      <div>
        <div class="main-title">短视频 8 阶段全生命周期状态机架构 (State Machine Pipeline)</div>
        <div class="sub-title">基于不可变快照、多模态 Vision 策划、单镜图生视频重做与门禁合成验证的工业级状态流转</div>
      </div>
    </div>
    <div class="meta-badges">
      <div class="badge badge-blue">8-STAGE DETERMINISTIC</div>
      <div class="badge badge-emerald">IDEMPOTENCY PROTECTED</div>
      <div class="badge badge-slate">TENANT ISOLATED</div>
    </div>
  </div>

  <!-- 4 Phases Grid -->
  <div class="phases-grid">
    <!-- PHASE 1 -->
    <div class="phase-col" id="col1">
      <div class="phase-header">
        <span class="phase-tag">PHASE 01</span>
        <span class="phase-name">输入与商业调研</span>
      </div>

      <!-- Stage 1 -->
      <div class="stage-card primary" id="stage1">
        <div class="stage-top">
          <span class="stage-seq blue">01 / STAGE</span>
          <span class="tag-highlight">快照固化</span>
        </div>
        <div class="stage-name">created</div>
        <div class="stage-desc">任务初始化与快照建立</div>
        <div class="spec-tags">
          <span class="tag">Theme (主题)</span>
          <span class="tag">Target Market</span>
          <span class="tag tag-highlight">Asset IDs (参考图)</span>
        </div>
      </div>

      <!-- Stage 2 -->
      <div class="stage-card row2" id="stage2">
        <div class="stage-top">
          <span class="stage-seq">02 / STAGE</span>
          <span class="tag">可选门禁</span>
        </div>
        <div class="stage-name">research</div>
        <div class="stage-desc">商业与竞品痛点调研</div>
        <div class="spec-tags">
          <span class="tag">行业高频痛点</span>
          <span class="tag">同类爆款拆解</span>
          <span class="tag">受众情绪心理</span>
        </div>
      </div>
    </div>

    <!-- PHASE 2 -->
    <div class="phase-col" id="col2">
      <div class="phase-header">
        <span class="phase-tag">PHASE 02</span>
        <span class="phase-name">策划与规格锁定</span>
      </div>

      <!-- Stage 3 -->
      <div class="stage-card primary" id="stage3">
        <div class="stage-top">
          <span class="stage-seq blue">03 / STAGE</span>
          <span class="tag-highlight">Vision 识图</span>
        </div>
        <div class="stage-name">script_proposal</div>
        <div class="stage-desc">多模态多方案策划生成</div>
        <div class="spec-tags">
          <span class="tag tag-highlight">方案 A/B/C 竞选</span>
          <span class="tag">黄金 3 秒 Hook</span>
          <span class="tag">卖点 Body & CTA</span>
        </div>
      </div>

      <!-- Stage 4 -->
      <div class="stage-card row2" id="stage4">
        <div class="stage-top">
          <span class="stage-seq">04 / STAGE</span>
          <span class="tag">工程门禁</span>
        </div>
        <div class="stage-name">script_confirmed</div>
        <div class="stage-desc">方案选择与技术规格锁定</div>
        <div class="spec-tags">
          <span class="tag">画幅 9:16 / 16:9</span>
          <span class="tag">时长 15s - 60s</span>
          <span class="tag">分辨率 1080p / 4K</span>
        </div>
      </div>
    </div>

    <!-- PHASE 3 -->
    <div class="phase-col" id="col3">
      <div class="phase-header">
        <span class="phase-tag">PHASE 03</span>
        <span class="phase-name">视听并行制作</span>
      </div>

      <!-- Stage 5 -->
      <div class="stage-card" id="stage5">
        <div class="stage-top">
          <span class="stage-seq">05 / STAGE</span>
          <span class="tag">T2I / I2I</span>
        </div>
        <div class="stage-name">keyframes</div>
        <div class="stage-desc">镜头切分与关键帧规划</div>
        <div class="spec-tags">
          <span class="tag">N 组分镜切分</span>
          <span class="tag tag-highlight">即梦 5.0 / GPT-Image</span>
          <span class="tag">首尾帧垫图渲染</span>
        </div>
      </div>

      <!-- Stage 6 -->
      <div class="stage-card primary row2" id="stage6">
        <div class="stage-top">
          <span class="stage-seq blue">06 / STAGE</span>
          <span class="tag-highlight">I2V 图生视频</span>
        </div>
        <div class="stage-name">shot_video</div>
        <div class="stage-desc">单镜视频生成与重做复核</div>
        <div class="spec-tags">
          <span class="tag tag-highlight">海螺 02 / 可灵 V3</span>
          <span class="tag">单镜参考图重做</span>
          <span class="tag">下游复核 (needs_review)</span>
        </div>
      </div>
    </div>

    <!-- PHASE 4 -->
    <div class="phase-col" id="col4">
      <div class="phase-header">
        <span class="phase-tag">PHASE 04</span>
        <span class="phase-name">合成与归档交付</span>
      </div>

      <!-- Stage 7 -->
      <div class="stage-card" id="stage7">
        <div class="stage-top">
          <span class="stage-seq">07 / STAGE</span>
          <span class="tag">质量门禁</span>
        </div>
        <div class="stage-name">composition</div>
        <div class="stage-desc">音画轨道对其与成片合成</div>
        <div class="spec-tags">
          <span class="tag">All Shots Ready 门禁</span>
          <span class="tag">多镜流式拼接合成</span>
          <span class="tag">画质与转码校正</span>
        </div>
      </div>

      <!-- Stage 8 -->
      <div class="stage-card accent row2" id="stage8">
        <div class="stage-top">
          <span class="stage-seq green">08 / STAGE</span>
          <span class="tag-green">交付成功</span>
        </div>
        <div class="stage-name">finished</div>
        <div class="stage-desc">成片安全归档与账单核算</div>
        <div class="spec-tags">
          <span class="tag tag-green">终版原画 MP4 下载</span>
          <span class="tag">版本快照固化 (vN)</span>
          <span class="tag">Token/GPU 消耗审计</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Bottom Security Safeguard Bar -->
  <div class="security-footer">
    <div class="guard-item">
      <div class="guard-dot"></div>
      <span class="guard-title">全链路幂等机制</span>
      <span>每个决策操作与重做派发均校验 idempotency_key，防网络重试重复扣费与状态错乱</span>
    </div>
    <div class="guard-item">
      <div class="guard-dot"></div>
      <span class="guard-title">乐观版本控制</span>
      <span>所有状态跃迁必须携带 expected_version，并发冲突立即触发 409 Conflict</span>
    </div>
    <div class="guard-item">
      <div class="guard-dot"></div>
      <span class="guard-title">租户防穿透</span>
      <span>SHA-256 私有目录寻址与严格 user_id 前置拦截</span>
    </div>
  </div>

  <!-- SVG Connectors will be injected here with exact mathematical coordinates -->
  __SVG_CONNECTORS__

</body>
</html>
"""

def compute_svg_connectors(rects):
    s = rects
    
    svg_defs = """
    <defs>
      <marker id="arrow-blue" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#2563eb"/>
      </marker>
      <marker id="arrow-slate" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#64748b"/>
      </marker>
      <marker id="arrow-green" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#10b981"/>
      </marker>
      <marker id="arrow-amber" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#d97706"/>
      </marker>
    </defs>
    """

    elements = []

    # 1. Vertical Intra-Phase Connectors (Stage 1->2, 3->4, 5->6, 7->8)
    def make_vertical_connector(s_from, s_to, text, color, marker_id, bg_color="#ffffff", stroke_color="#cbd5e1", text_color="#475569"):
        cx = s_from["centerX"]
        y1 = s_from["bottom"]
        y2 = s_to["top"]
        mid_y = (y1 + y2) / 2
        badge_w = len(text) * 11 + 16
        badge_h = 20
        badge_x = cx - badge_w / 2
        badge_y = mid_y - badge_h / 2
        
        line1 = f'<path d="M {cx:.1f} {y1:.1f} L {cx:.1f} {badge_y:.1f}" fill="none" stroke="{color}" stroke-width="2"/>'
        line2 = f'<path d="M {cx:.1f} {badge_y + badge_h:.1f} L {cx:.1f} {y2 - 2:.1f}" fill="none" stroke="{color}" stroke-width="2" marker-end="url(#{marker_id})"/>'
        rect = f'<rect x="{badge_x:.1f}" y="{badge_y:.1f}" width="{badge_w:.1f}" height="{badge_h:.1f}" rx="4" fill="{bg_color}" stroke="{stroke_color}" stroke-width="1"/>'
        txt = f'<text x="{cx:.1f}" y="{mid_y + 4:.1f}" font-size="10.5" font-weight="600" fill="{text_color}" font-family="-apple-system, sans-serif" text-anchor="middle">{text}</text>'
        return f"{line1}\n{line2}\n{rect}\n{txt}"

    elements.append(make_vertical_connector(
        s["stage1"], s["stage2"], "启动调研", "#64748b", "arrow-slate",
        bg_color="#ffffff", stroke_color="#cbd5e1", text_color="#475569"
    ))
    elements.append(make_vertical_connector(
        s["stage3"], s["stage4"], "选定方案 (Lock)", "#2563eb", "arrow-blue",
        bg_color="#eff6ff", stroke_color="#bfdbfe", text_color="#1d4ed8"
    ))
    elements.append(make_vertical_connector(
        s["stage5"], s["stage6"], "首尾帧准备", "#2563eb", "arrow-blue",
        bg_color="#eff6ff", stroke_color="#bfdbfe", text_color="#1d4ed8"
    ))
    elements.append(make_vertical_connector(
        s["stage7"], s["stage8"], "门禁合成", "#10b981", "arrow-green",
        bg_color="#ecfdf5", stroke_color="#a7f3d0", text_color="#047857"
    ))

    # 2. Skip Research Bypass: Stage 1 (right) -> Stage 3 (left)
    # Both are on Row 1. Straight horizontal line at y = Stage 1 top + 34px.
    # The badge sits neatly floating ABOVE the dashed line so the line is continuous and uninterrupted.
    x1 = s["stage1"]["right"]
    x2 = s["stage3"]["left"]
    bypass_y = s["stage1"]["top"] + 34
    mid_x_12 = (x1 + x2) / 2
    b_text = "skip_research"
    bw = len(b_text) * 6.8 + 14
    bh = 17
    bx = mid_x_12 - bw / 2
    by = bypass_y - bh - 5 # 5px above the dashed line

    line_skip = f'<path d="M {x1:.1f} {bypass_y:.1f} L {x2 - 2:.1f} {bypass_y:.1f}" fill="none" stroke="#2563eb" stroke-width="2" stroke-dasharray="4 3" marker-end="url(#arrow-blue)"/>'
    rect_skip = f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="#eff6ff" stroke="#bfdbfe" stroke-width="1"/>'
    txt_skip = f'<text x="{mid_x_12:.1f}" y="{by + 12:.1f}" font-size="10" font-weight="600" fill="#1d4ed8" font-family="ui-monospace, monospace" text-anchor="middle">{b_text}</text>'
    elements.extend([line_skip, rect_skip, txt_skip])

    # 3. Inter-Phase Risers (Bottom row to top row of next column)
    def make_inter_phase_riser(s_from, s_to, text, color, marker_id, bg_color="#ffffff", stroke_color="#cbd5e1", text_color="#475569"):
        y_out = s_from["centerY"]
        y_in = s_to["centerY"] + 18
        x_start = s_from["right"]
        x_end = s_to["left"]
        channel_x = (x_start + x_end) / 2
        
        path_d = f"M {x_start:.1f} {y_out:.1f} L {channel_x - 8:.1f} {y_out:.1f} Q {channel_x:.1f} {y_out:.1f} {channel_x:.1f} {y_out - 8:.1f} L {channel_x:.1f} {y_in + 8:.1f} Q {channel_x:.1f} {y_in:.1f} {channel_x + 8:.1f} {y_in:.1f} L {x_end - 2:.1f} {y_in:.1f}"
        line = f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="2" marker-end="url(#{marker_id})"/>'

        badge_y = (y_out + y_in) / 2
        bw = len(text) * 11 + 16
        bh = 20
        bx = channel_x - bw / 2
        by = badge_y - bh / 2
        rect = f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="4" fill="{bg_color}" stroke="{stroke_color}" stroke-width="1"/>'
        txt = f'<text x="{channel_x:.1f}" y="{badge_y + 4:.1f}" font-size="10.5" font-weight="600" fill="{text_color}" font-family="-apple-system, sans-serif" text-anchor="middle">{text}</text>'
        return f"{line}\n{rect}\n{txt}"

    elements.append(make_inter_phase_riser(
        s["stage2"], s["stage3"], "调研完成", "#64748b", "arrow-slate",
        bg_color="#ffffff", stroke_color="#cbd5e1", text_color="#475569"
    ))
    elements.append(make_inter_phase_riser(
        s["stage4"], s["stage5"], "推进分镜", "#2563eb", "arrow-blue",
        bg_color="#eff6ff", stroke_color="#bfdbfe", text_color="#1d4ed8"
    ))
    elements.append(make_inter_phase_riser(
        s["stage6"], s["stage7"], "分镜全量验收", "#2563eb", "arrow-blue",
        bg_color="#eff6ff", stroke_color="#bfdbfe", text_color="#1d4ed8"
    ))

    # 4. Stage 6 Single Shot Redo Loop (Bottom loop)
    st6 = s["stage6"]
    loop_start_x = st6["centerX"] - 50
    loop_end_x = st6["centerX"] + 50
    loop_start_y = st6["bottom"]
    loop_end_y = st6["bottom"]
    deep_y = loop_start_y + 36
    
    redo_d = f"M {loop_start_x:.1f} {loop_start_y:.1f} L {loop_start_x:.1f} {deep_y - 6:.1f} Q {loop_start_x:.1f} {deep_y:.1f} {loop_start_x + 6:.1f} {deep_y:.1f} L {loop_end_x - 6:.1f} {deep_y:.1f} Q {loop_end_x:.1f} {deep_y:.1f} {loop_end_x:.1f} {deep_y - 6:.1f} L {loop_end_x:.1f} {loop_end_y + 2:.1f}"
    redo_line = f'<path d="{redo_d}" fill="none" stroke="#d97706" stroke-width="2" stroke-dasharray="3 3" marker-end="url(#arrow-amber)"/>'
    
    redo_text = "单镜参考图重做 (I2V Redo)"
    rbw = len(redo_text) * 7.2 + 20
    rbh = 20
    rbx = st6["centerX"] - rbw / 2
    rby = deep_y - rbh / 2
    redo_rect = f'<rect x="{rbx:.1f}" y="{rby:.1f}" width="{rbw:.1f}" height="{rbh:.1f}" rx="4" fill="#fffbeb" stroke="#fde68a" stroke-width="1"/>'
    redo_txt = f'<text x="{st6["centerX"]:.1f}" y="{deep_y + 4:.1f}" font-size="10" font-weight="600" fill="#b45309" font-family="-apple-system, sans-serif" text-anchor="middle">{redo_text}</text>'
    
    elements.extend([redo_line, redo_rect, redo_txt])

    svg = f'<svg class="connectors">\n{svg_defs}\n' + "\n".join(elements) + "\n</svg>"
    return svg

ARCHITECTURE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>FormaAI System Architecture</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    width: 1440px;
    height: 980px;
    background: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    color: #1e293b;
    padding: 32px 40px;
    overflow: hidden;
  }

  .header-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 2px solid #e2e8f0;
    padding-bottom: 16px;
    margin-bottom: 20px;
  }
  .title-group { display: flex; align-items: center; gap: 12px; }
  .logo-badge {
    background: #0f172a;
    color: #ffffff;
    font-weight: 800;
    font-size: 14px;
    padding: 6px 12px;
    border-radius: 6px;
    letter-spacing: 0.5px;
  }
  .main-title {
    font-size: 20px;
    font-weight: 700;
    color: #0f172a;
    letter-spacing: -0.3px;
  }
  .sub-title {
    font-size: 12px;
    color: #64748b;
    margin-top: 2px;
  }
  .meta-badges { display: flex; gap: 8px; }
  .badge {
    font-size: 11px;
    font-family: ui-monospace, monospace;
    padding: 4px 10px;
    border-radius: 4px;
    font-weight: 600;
  }
  .badge-blue { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
  .badge-purple { background: #faf5ff; color: #7e22ce; border: 1px solid #e9d5ff; }
  .badge-slate { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }

  /* Architecture Layers */
  .arch-container {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .arch-tier {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 10px;
    padding: 12px 18px;
    display: flex;
    align-items: center;
    gap: 20px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    position: relative;
  }

  .tier-label-box {
    width: 170px;
    flex-shrink: 0;
    border-right: 1.5px solid #e2e8f0;
    padding-right: 14px;
  }
  .tier-seq {
    font-size: 10px;
    font-weight: 700;
    font-family: ui-monospace, monospace;
    color: #64748b;
    letter-spacing: 0.5px;
  }
  .tier-name {
    font-size: 13px;
    font-weight: 700;
    color: #0f172a;
    margin-top: 2px;
  }
  .tier-sub {
    font-size: 10px;
    color: #94a3b8;
    margin-top: 1px;
  }

  .tier-content {
    flex: 1;
    display: grid;
    gap: 12px;
  }

  .grid-4 { grid-template-columns: repeat(4, 1fr); }
  .grid-3 { grid-template-columns: repeat(3, 1fr); }

  .arch-card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 9px 12px;
  }
  .arch-card.highlight {
    background: #eff6ff;
    border-color: #bfdbfe;
  }
  .arch-card.green {
    background: #ecfdf5;
    border-color: #a7f3d0;
  }
  .arch-card.purple {
    background: #faf5ff;
    border-color: #e9d5ff;
  }
  .arch-card.amber {
    background: #fffbeb;
    border-color: #fde68a;
  }

  .card-title {
    font-size: 12px;
    font-weight: 700;
    color: #1e293b;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .card-desc {
    font-size: 10.5px;
    color: #64748b;
    margin-top: 4px;
    line-height: 1.4;
  }
  .card-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 5px;
  }
  .chip {
    font-size: 9.5px;
    font-family: ui-monospace, monospace;
    padding: 1px 5px;
    border-radius: 3px;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    color: #334155;
  }
  .chip-blue { background: #dbeafe; border-color: #bfdbfe; color: #1e40af; font-weight: 600; }
  .chip-green { background: #d1fae5; border-color: #a7f3d0; color: #065f46; font-weight: 600; }
  .chip-amber { background: #fef3c7; border-color: #fde68a; color: #92400e; font-weight: 600; }

  .ha-ribbon {
    font-size: 10px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-weight: 600;
  }
  .ha-arrow { color: #2563eb; font-weight: 800; }

  .arch-footer {
    margin-top: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 10px 18px;
    font-size: 11px;
    color: #475569;
  }
</style>
</head>
<body>

  <!-- Header -->
  <div class="header-bar">
    <div class="title-group">
      <div class="logo-badge">ARCHITECTURE</div>
      <div>
        <div class="main-title">FormaAI 企业级系统分层架构拓扑图 (Clean Architecture Topology)</div>
        <div class="sub-title">端到端工业级解耦：UI 展现层、零信任安全边界、生产状态机核心、302.AI 三模态高可用网关与持久化基础</div>
      </div>
    </div>
    <div class="meta-badges">
      <div class="badge badge-blue">CLEAN ARCHITECTURE</div>
      <div class="badge badge-purple">TRI-MODAL HA GATEWAY</div>
      <div class="badge badge-slate">ZERO-TRUST AUDITED</div>
    </div>
  </div>

  <div class="arch-container">

    <!-- TIER 1 -->
    <div class="arch-tier">
      <div class="tier-label-box">
        <div class="tier-seq">TIER 01 / CLIENT</div>
        <div class="tier-name">终端展现交互层</div>
        <div class="tier-sub">Vue 3.5 + TS + Tailwind</div>
      </div>
      <div class="tier-content grid-4">
        <div class="arch-card">
          <div class="card-title">生产监控控制台</div>
          <div class="card-desc">真实数据看板、任务状态漏斗与品类热度分布</div>
          <div class="card-chips">
            <span class="chip">DashboardView.vue</span>
            <span class="chip">Chartless Metric</span>
          </div>
        </div>
        <div class="arch-card highlight">
          <div class="card-title">8 阶段制作工作台</div>
          <div class="card-desc">紧凑分镜编排、单镜状态追踪与成片门禁合成</div>
          <div class="card-chips">
            <span class="chip chip-blue">TaskStudioView.vue</span>
            <span class="chip">VideoAttempt Log</span>
          </div>
        </div>
        <div class="arch-card amber">
          <div class="card-title">单镜重做与参考图</div>
          <div class="card-desc">品类素材直选/外链垫图、I2V 图生视频参数配置</div>
          <div class="card-chips">
            <span class="chip chip-amber">I2V Reference</span>
            <span class="chip">Prompt Adjustment</span>
          </div>
        </div>
        <div class="arch-card">
          <div class="card-title">品类与素材媒体库</div>
          <div class="card-desc">四大商业矩阵预设、结构化文案与视觉素材管理</div>
          <div class="card-chips">
            <span class="chip">LibraryView.vue</span>
            <span class="chip">Preset Catalog</span>
          </div>
        </div>
      </div>
    </div>

    <!-- TIER 2 -->
    <div class="arch-tier">
      <div class="tier-label-box">
        <div class="tier-seq">TIER 02 / GATEWAY</div>
        <div class="tier-name">接入网关与安全边界</div>
        <div class="tier-sub">FastAPI + Redis Guard</div>
      </div>
      <div class="tier-content grid-4">
        <div class="arch-card green">
          <div class="card-title">双 Token 无状态认证</div>
          <div class="card-desc">Access JWT (15m 短效) + Refresh Token (7d 长效) 轮换</div>
          <div class="card-chips">
            <span class="chip chip-green">HTTPBearer</span>
            <span class="chip">HS256 Signing</span>
          </div>
        </div>
        <div class="arch-card">
          <div class="card-title">注销冷静期风控拦截</div>
          <div class="card-desc">Redis 毫秒级会话黑名单，冷静期强制剥夺写权限</div>
          <div class="card-chips">
            <span class="chip">SessionManager</span>
            <span class="chip">Cooling-off Block</span>
          </div>
        </div>
        <div class="arch-card highlight">
          <div class="card-title">业务幂等重放拦截器</div>
          <div class="card-desc">全链路校验 idempotency_key，一致返快照，冲突报 409</div>
          <div class="card-chips">
            <span class="chip chip-blue">Idempotent Replay</span>
            <span class="chip">Collision Guard</span>
          </div>
        </div>
        <div class="arch-card">
          <div class="card-title">存储寻址防穿透沙箱</div>
          <div class="card-desc">SHA-256 哈希散列与多租户隔离目录，根治路径遍历</div>
          <div class="card-chips">
            <span class="chip">Safe Path Resolver</span>
            <span class="chip">No Path Traversal</span>
          </div>
        </div>
      </div>
    </div>

    <!-- TIER 3 -->
    <div class="arch-tier">
      <div class="tier-label-box">
        <div class="tier-seq">TIER 03 / DOMAIN</div>
        <div class="tier-name">领域模型与状态机引擎</div>
        <div class="tier-sub">Clean Architecture Core</div>
      </div>
      <div class="tier-content grid-4">
        <div class="arch-card">
          <div class="card-title">账号与租户领域</div>
          <div class="card-desc">UserAccount、Argon2id 密码哈希、多租户主键前置约束</div>
          <div class="card-chips">
            <span class="chip">Identity Domain</span>
            <span class="chip">Tenant Verifier</span>
          </div>
        </div>
        <div class="arch-card">
          <div class="card-title">分类与素材资产域</div>
          <div class="card-desc">Category、Material、Asset，提供安全流式字节传输</div>
          <div class="card-chips">
            <span class="chip">CategoryDTO</span>
            <span class="chip">Streaming File API</span>
          </div>
        </div>
        <div class="arch-card highlight">
          <div class="card-title">8 阶段有限状态机</div>
          <div class="card-desc">VideoTask 状态跃迁、乐观锁版本控制 (version) 与决策审计</div>
          <div class="card-chips">
            <span class="chip chip-blue">Stage Transition</span>
            <span class="chip">Optimistic Lock</span>
          </div>
        </div>
        <div class="arch-card purple">
          <div class="card-title">分镜制造与合成门禁</div>
          <div class="card-desc">VideoShot、VideoAttempt、下游级联复核通知与成片拼接</div>
          <div class="card-chips">
            <span class="chip chip-purple">needs_review</span>
            <span class="chip">Composition Gate</span>
          </div>
        </div>
      </div>
    </div>

    <!-- TIER 4 -->
    <div class="arch-tier">
      <div class="tier-label-box">
        <div class="tier-seq">TIER 04 / MODEL HA</div>
        <div class="tier-name">三模态高可用网关</div>
        <div class="tier-sub">302.AI Failover Engine</div>
      </div>
      <div class="tier-content grid-3">
        <div class="arch-card highlight">
          <div class="card-title">LLM 文本策划与多模态通道</div>
          <div class="card-desc">
            <div class="ha-ribbon">
              <span>DeepSeek-Chat (主)</span>
              <span class="ha-arrow">➔</span>
              <span>GPT-4o (备/Vision)</span>
            </div>
            <div style="margin-top: 3px;">带参考图时自动识别模型视觉通道，生成方案 A/B/C</div>
          </div>
          <div class="card-chips">
            <span class="chip chip-blue">OpenAI Vision Format</span>
            <span class="chip">1500 Tokens</span>
          </div>
        </div>
        <div class="arch-card green">
          <div class="card-title">T2I / I2I 分镜生图通道</div>
          <div class="card-desc">
            <div class="ha-ribbon">
              <span>即梦 5.0 SeaDream (主)</span>
              <span class="ha-arrow">➔</span>
              <span>GPT-Image (备)</span>
            </div>
            <div style="margin-top: 3px;">分镜首尾帧垫图渲染，主模型超时自动熔断切换备选</div>
          </div>
          <div class="card-chips">
            <span class="chip chip-green">302ai CLI Image</span>
            <span class="chip">Aspect 9:16</span>
          </div>
        </div>
        <div class="arch-card amber">
          <div class="card-title">T2V / I2V 视频渲染通道</div>
          <div class="card-desc">
            <div class="ha-ribbon">
              <span>海螺 MiniMax 02 (主)</span>
              <span class="ha-arrow">➔</span>
              <span>可灵 Kling V3 (备)</span>
            </div>
            <div style="margin-top: 3px;">异步任务轮询，透传 --image 参数原生执行图生视频</div>
          </div>
          <div class="card-chips">
            <span class="chip chip-amber">Native I2V</span>
            <span class="chip">Exponential Backoff</span>
          </div>
        </div>
      </div>
    </div>

    <!-- TIER 5 -->
    <div class="arch-tier">
      <div class="tier-label-box">
        <div class="tier-seq">TIER 05 / STORAGE</div>
        <div class="tier-name">数据与基础设施层</div>
        <div class="tier-sub">Persistence & Containers</div>
      </div>
      <div class="tier-content grid-4">
        <div class="arch-card">
          <div class="card-title">MySQL 8.4 LTS 关系存储</div>
          <div class="card-desc">7 个演进版本迁移，UTC 微秒精度，严格外键与 CHECK 约束</div>
          <div class="card-chips">
            <span class="chip">Alembic Head: 20261123_07</span>
            <span class="chip">Pool Size: 10</span>
          </div>
        </div>
        <div class="arch-card">
          <div class="card-title">Redis 7.4 会话与缓存</div>
          <div class="card-desc">会话注销冷静期风控状态、全局锁、高可用键值缓存</div>
          <div class="card-chips">
            <span class="chip">Session Cache</span>
            <span class="chip">TTL Auto-Expire</span>
          </div>
        </div>
        <div class="arch-card">
          <div class="card-title">安全对象与本地存储</div>
          <div class="card-desc">物理快照分区、成片 MP4 流式归档与 SHA-256 完整性校验</div>
          <div class="card-chips">
            <span class="chip">LocalStorageService</span>
            <span class="chip">Streaming Response</span>
          </div>
        </div>
        <div class="arch-card">
          <div class="card-title">容器化编排环境</div>
          <div class="card-desc">Docker Compose 一键启动 MySQL + Redis，配合 Uvicorn 多进程</div>
          <div class="card-chips">
            <span class="chip">docker-compose.yml</span>
            <span class="chip">Nginx Reverse Proxy</span>
          </div>
        </div>
      </div>
    </div>

  </div>

  <!-- Footer -->
  <div class="arch-footer">
    <div><strong>高可用防抖策略：</strong>瞬时 429 / 5xx 故障自动触发 (2^(n-1)*0.5s + jitter) 指数退避重试，重试耗尽自动熔断至备选通道</div>
    <div><strong>全量自动化测试基线：</strong>345+ 单元与集成测试套件 100% PASS · 零 Mock 生产准入预检全绿</div>
  </div>

</body>
</html>
"""

def render_all_diagrams():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # 1. State Machine Diagram (Dynamic Precise Layout Measurement)
        page1 = browser.new_page(viewport={"width": 1440, "height": 760}, device_scale_factor=2)
        initial_html = STATE_MACHINE_HTML.replace("__SVG_CONNECTORS__", "")
        page1.set_content(initial_html)
        
        rects = {}
        for s_id in ["stage1", "stage2", "stage3", "stage4", "stage5", "stage6", "stage7", "stage8"]:
            r = page1.eval_on_selector(f"#{s_id}", "el => el.getBoundingClientRect()")
            rects[s_id] = {
                "left": r["left"],
                "right": r["right"],
                "top": r["top"],
                "bottom": r["bottom"],
                "width": r["width"],
                "height": r["height"],
                "centerX": (r["left"] + r["right"]) / 2,
                "centerY": (r["top"] + r["bottom"]) / 2,
            }

        svg = compute_svg_connectors(rects)
        full_html = STATE_MACHINE_HTML.replace("__SVG_CONNECTORS__", svg)
        page1.set_content(full_html)
        page1.wait_for_timeout(300)

        out_sm = OUTPUT_DIR / "formaai-state-machine.png"
        page1.screenshot(path=str(out_sm), full_page=True)
        print(f"Rendered: {out_sm}")
        page1.close()

        # 2. System Architecture Diagram
        page2 = browser.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=2)
        page2.set_content(ARCHITECTURE_HTML)
        page2.wait_for_timeout(300)
        out_arch = OUTPUT_DIR / "formaai-architecture.png"
        page2.screenshot(path=str(out_arch), full_page=True)
        print(f"Rendered: {out_arch}")
        page2.close()

        browser.close()

if __name__ == "__main__":
    render_all_diagrams()
