"""把分析结果渲染成单文件 HTML（结构与应用页面一致）。

两种模式：
- mode="standalone"：**下载用**的完整独立文件，顶部带应用导航，包含
  「分析结果」与「完整报告」两个视图，交互（切换、查看观点详情、评论分页）全部内联实现，
  不依赖任何外部资源，双击即可离线查看；
- mode="report"：只有报告正文（无外层导航），供应用内 iframe 直接嵌入展示。

页面结构、配色与排版刻意与 Vue 前端（/result、/report）保持一致，
因此下载下来的文件看起来就是网页本身。
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

STANCE_LABELS = {
    "support": "支持",
    "oppose": "反对",
    "conditional": "有条件",
    "neutral": "中立",
    "unclear": "不明",
}

# 生成器版本：模板结构变化时递增，用于让服务端丢弃旧的 HTML 缓存
GENERATOR_VERSION = "3"

PALETTE = [
    "#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#14b8a6",
]

# 每个观点在详情里默认展示多少条原始评论（分页大小）
COMMENT_PAGE_SIZE = 20

STYLE = """
* { box-sizing: border-box; }
body { margin:0; background:#f5f7fb; color:#1f2329; line-height:1.7;
  font-family:'PingFang SC','Microsoft YaHei','Helvetica Neue',Arial,sans-serif; }
a { color:#2563eb; }
.app-header { background:#fff; border-bottom:1px solid #e5e7eb; position:sticky; top:0; z-index:20; }
.header-inner { max-width:1180px; margin:0 auto; padding:14px 16px; display:flex; align-items:center; gap:16px; }
.brand { font-size:18px; font-weight:600; color:#1f2329; text-decoration:none; white-space:nowrap; }
.nav { display:flex; gap:4px; }
.nav a { color:#4b5563; text-decoration:none; font-size:14px; padding:6px 10px; border-radius:6px; white-space:nowrap; }
.nav a.active { color:#2563eb; background:#eff6ff; font-weight:600; }
.tagline { margin-left:auto; color:#6b7280; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.page { max-width:1180px; margin:0 auto; padding:24px 16px 64px; }
.card { background:#fff; border-radius:12px; padding:20px; box-shadow:0 1px 4px rgba(15,23,42,.08); margin-bottom:16px; }
.muted { color:#6b7280; font-size:13px; }
.section-title { font-size:16px; font-weight:600; margin:0 0 12px; }
.head { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }
.head-actions { display:flex; gap:8px; flex-wrap:wrap; }
.btn { display:inline-block; border:1px solid #dcdfe6; background:#fff; color:#1f2329; border-radius:6px;
  padding:6px 14px; font-size:14px; cursor:pointer; text-decoration:none; }
.btn:hover { color:#2563eb; border-color:#a8c7fa; }
.btn.primary { background:#2563eb; border-color:#2563eb; color:#fff; }
.btn.primary:hover { background:#1d4ed8; color:#fff; }
.cards { display:flex; flex-wrap:wrap; gap:12px; }
.stat { flex:1 1 160px; background:#fff; border-radius:12px; padding:14px 16px;
  box-shadow:0 1px 4px rgba(15,23,42,.08); }
.stat .k { color:#6b7280; font-size:13px; }
.stat .v { font-size:24px; font-weight:600; color:#2563eb; }
.stat .h { color:#9ca3af; font-size:12px; }
table { border-collapse:collapse; width:100%; margin:8px 0; font-size:13px; }
th, td { border:1px solid #e5e7eb; padding:7px 9px; text-align:left; vertical-align:top; }
th { background:#f8fafc; font-weight:600; color:#374151; }
td.num, th.num { text-align:right; white-space:nowrap; }
tr.viewpoint-row:hover { background:#f8fafc; }
.tag { display:inline-block; font-size:12px; padding:1px 8px; border-radius:10px; background:#eff6ff; color:#1d4ed8; }
.tag.oppose { background:#fef2f2; color:#b91c1c; }
.tag.conditional { background:#fffbeb; color:#b45309; }
.tag.neutral { background:#f1f5f9; color:#475569; }
blockquote { margin:6px 0; padding:8px 12px; background:#f8fafc; border-left:3px solid #93c5fd;
  border-radius:4px; font-size:13px; }
.note { background:#f8fafc; border-left:3px solid #93c5fd; padding:8px 12px; color:#475569; font-size:13px; margin:8px 0; }
.charts { display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:16px; }
.chart-title { text-align:center; font-size:14px; font-weight:600; margin:0 0 4px; }
details { margin-top:8px; }
summary { cursor:pointer; color:#2563eb; font-size:13px; }
code { background:#f1f5f9; padding:1px 4px; border-radius:4px; font-size:12px; }
pre { background:#0f172a; color:#e2e8f0; padding:12px; border-radius:8px; overflow:auto; font-size:12px; }
footer { color:#6b7280; font-size:12px; text-align:center; padding:16px 0; }
.view { display:none; }
.view.active { display:block; }
/* 观点详情：与前端抽屉一致的右侧滑出面板 */
.drawer-mask { position:fixed; inset:0; background:rgba(15,23,42,.35); display:none; z-index:40; }
.drawer-mask.open { display:block; }
.drawer { position:fixed; top:0; right:0; bottom:0; width:min(720px,100%); background:#f5f7fb;
  box-shadow:-4px 0 16px rgba(15,23,42,.15); transform:translateX(100%); transition:transform .2s ease;
  z-index:41; overflow-y:auto; }
.drawer.open { transform:translateX(0); }
.drawer-head { position:sticky; top:0; background:#fff; border-bottom:1px solid #e5e7eb; padding:14px 20px;
  display:flex; justify-content:space-between; align-items:center; gap:12px; }
.drawer-title { font-size:15px; font-weight:600; }
.drawer-body { padding:16px 20px 40px; }
.pager { display:flex; justify-content:flex-end; align-items:center; gap:8px; margin-top:12px; font-size:13px; color:#6b7280; }
.bar-row { display:flex; align-items:center; gap:8px; font-size:13px; margin:4px 0; }
.bar-row .label { width:190px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bar-row .track { flex:1; background:#eef2f7; border-radius:4px; height:14px; }
.bar-row .fill { height:14px; border-radius:4px; }
.bar-row .val { width:120px; text-align:right; color:#6b7280; }
@media (max-width:900px) { .tagline { display:none; } .charts { grid-template-columns:1fr; } }
@media print { body { background:#fff; } .card, .stat { box-shadow:none; border:1px solid #e5e7eb; }
  .app-header, .drawer, .drawer-mask { display:none !important; } .view { display:block !important; } }
"""

SCRIPT = """
(function () {
  var data = JSON.parse(document.getElementById('analysis-data').textContent);
  var pageSize = __PAGE_SIZE__;
  var currentComments = [];
  var currentPage = 1;
  var currentViewpoint = null;

  function esc(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function stanceLabel(stance) {
    var map = { support: '支持', oppose: '反对', conditional: '有条件', neutral: '中立', unclear: '不明' };
    return map[stance] || stance || '不明';
  }

  function stanceClass(stance) {
    return stance === 'oppose' || stance === 'conditional' || stance === 'neutral' ? stance : '';
  }

  // ---- 视图切换（#result / #report）----
  function showView(name) {
    var target = name === 'report' ? 'report' : 'result';
    document.querySelectorAll('.view').forEach(function (el) {
      el.classList.toggle('active', el.id === 'view-' + target);
    });
    document.querySelectorAll('.nav a').forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('href') === '#' + target);
    });
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', function () { showView(location.hash.replace('#', '')); });
  showView(location.hash.replace('#', ''));

  // ---- 观点详情 ----
  function renderReasons(reasons) {
    if (!reasons || !reasons.length) return '<p class="muted">该观点下没有识别到明确理由。</p>';
    var rows = reasons.map(function (r) {
      return '<tr><td>' + esc(r.name) + '</td><td class="num">' + r.comment_count +
        '</td><td class="num">' + r.percentage + '%</td></tr>';
    }).join('');
    return '<table><thead><tr><th>理由</th><th class="num">评论数</th><th class="num">占该观点</th></tr></thead><tbody>' +
      rows + '</tbody></table>';
  }

  function renderRepresentative(list) {
    if (!list || !list.length) return '<p class="muted">暂无代表性评论。</p>';
    return list.map(function (text) { return '<blockquote>' + esc(text) + '</blockquote>'; }).join('');
  }

  function renderCommentPage() {
    var total = currentComments.length;
    var pages = Math.max(1, Math.ceil(total / pageSize));
    if (currentPage > pages) currentPage = pages;
    var start = (currentPage - 1) * pageSize;
    var slice = currentComments.slice(start, start + pageSize);
    var rows = slice.map(function (c) {
      return '<tr><td><code>' + esc(c.comment_id) + '</code></td><td>' + esc(c.content) +
        '</td><td class="num">' + (c.like_count || 0) + '</td></tr>';
    }).join('');
    document.getElementById('drawer-comments').innerHTML =
      '<table><thead><tr><th>comment_id</th><th>原始评论</th><th class="num">点赞</th></tr></thead><tbody>' +
      rows + '</tbody></table>';
    document.getElementById('drawer-pager').innerHTML =
      '<span>共 ' + total + ' 条，第 ' + currentPage + ' / ' + pages + ' 页</span>' +
      '<button class="btn" id="page-prev"' + (currentPage <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<button class="btn" id="page-next"' + (currentPage >= pages ? ' disabled' : '') + '>下一页</button>';
    var prev = document.getElementById('page-prev');
    var next = document.getElementById('page-next');
    if (prev) prev.onclick = function () { if (currentPage > 1) { currentPage--; renderCommentPage(); } };
    if (next) next.onclick = function () { if (currentPage < pages) { currentPage++; renderCommentPage(); } };
  }

  function openViewpoint(id) {
    var vp = (data.viewpoints || []).filter(function (v) { return v.viewpoint_id === id; })[0];
    if (!vp) return;
    currentViewpoint = vp;
    currentComments = (vp.comment_ids || []).map(function (cid) {
      return data.comment_index[cid] || { comment_id: cid, content: '（该评论不在清洗结果中）', like_count: 0 };
    });
    currentPage = 1;
    document.getElementById('drawer-title').textContent = vp.viewpoint_id + ' ' + vp.name;
    document.getElementById('drawer-summary').innerHTML =
      '<p class="section-title">' + esc(vp.viewpoint_id) + '　' + esc(vp.name) + '</p>' +
      '<p class="muted">' + esc(vp.description || '') + '</p>' +
      '<p><span class="muted">评论数量：</span><b>' + vp.comment_count + '</b> 条' +
      '<span class="muted" style="margin-left:12px">占有效评论：</span><b>' + vp.percentage + '%</b>' +
      '<span class="muted" style="margin-left:12px">立场倾向：</span><b>' + stanceLabel(vp.stance) + '</b></p>';
    document.getElementById('drawer-reasons').innerHTML = renderReasons(vp.reasons);
    document.getElementById('drawer-representative').innerHTML = renderRepresentative(vp.representative_comments);
    renderCommentPage();
    document.getElementById('drawer-mask').classList.add('open');
    document.getElementById('drawer').classList.add('open');
  }

  function closeDrawer() {
    document.getElementById('drawer-mask').classList.remove('open');
    document.getElementById('drawer').classList.remove('open');
  }

  document.querySelectorAll('[data-viewpoint]').forEach(function (el) {
    el.addEventListener('click', function () { openViewpoint(el.getAttribute('data-viewpoint')); });
  });
  var mask = document.getElementById('drawer-mask');
  if (mask) mask.addEventListener('click', closeDrawer);
  var closeBtn = document.getElementById('drawer-close');
  if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (event) { if (event.key === 'Escape') closeDrawer(); });

  // ---- 观点表格：关键字过滤 ----
  var filterInput = document.getElementById('viewpoint-filter');
  if (filterInput) {
    filterInput.addEventListener('input', function () {
      var keyword = filterInput.value.trim();
      document.querySelectorAll('tr.viewpoint-row').forEach(function (row) {
        row.style.display = !keyword || row.dataset.name.indexOf(keyword) >= 0 ? '' : 'none';
      });
    });
  }

  // ---- 导出内嵌数据（离线文件里等价于“打包下载 JSON”）----
  var exportBtn = document.getElementById('export-json');
  if (exportBtn) {
    exportBtn.addEventListener('click', function () {
      var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      var link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'analysis_results.json';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
    });
  }
})();
"""


def _esc(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def _stance(value: str) -> tuple[str, str]:
    label = STANCE_LABELS.get(value or "", value or "不明")
    css = value if value in ("oppose", "conditional", "neutral") else ""
    return label, css


def _json_script(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f'<script id="analysis-data" type="application/json">{text}</script>'


# ----------------------------------------------------------------- 概况与图表
def _summary_cards(summary: dict) -> str:
    cards = [
        ("评论总数", summary.get("raw_comment_count", 0), "原始文件条数"),
        ("有效评论", summary.get("valid_comment_count", 0), "进入语义分析"),
        ("无关评论", summary.get("irrelevant_comment_count", 0), "与主题无关"),
        ("无法判断", summary.get("unclear_comment_count", 0), "没有明确观点"),
        ("最终观点数", summary.get("viewpoint_count", 0), "归并后核心观点"),
        ("观点-评论关联", summary.get("viewpoint_comment_link_count", 0), "一条评论可含多个观点"),
    ]
    items = "".join(
        f"<div class='stat'><div class='k'>{_esc(key)}</div><div class='v'>{_esc(value)}</div>"
        f"<div class='h'>{_esc(hint)}</div></div>"
        for key, value, hint in cards
    )
    return f"<div class='cards'>{items}</div>"


def _bar_chart(viewpoints: list[dict], top: int = 15) -> str:
    rows = viewpoints[:top]
    if not rows:
        return "<p class='muted'>暂无数据</p>"
    maximum = max((v.get("comment_count") or 0) for v in rows) or 1
    parts = [
        "<div class='chart-title'>各观点评论数量（Top %d）</div>" % min(top, len(rows))
    ]
    for index, viewpoint in enumerate(rows):
        count = viewpoint.get("comment_count") or 0
        width = max(2.0, round(count / maximum * 100, 1))
        color = PALETTE[index % len(PALETTE)]
        parts.append(
            "<div class='bar-row'>"
            f"<div class='label' title='{_esc(viewpoint.get('name'))}'>{_esc(viewpoint.get('name'))}</div>"
            f"<div class='track'><div class='fill' style='width:{width}%;background:{color}'></div></div>"
            f"<div class='val'>{count} 条 · {_esc(viewpoint.get('percentage'))}%</div>"
            "</div>"
        )
    return "".join(parts)


def _donut_chart(viewpoints: list[dict], top: int = 10) -> str:
    rows = viewpoints[:top]
    if not rows:
        return "<p class='muted'>暂无数据</p>"
    total = sum((v.get("comment_count") or 0) for v in rows) or 1
    radius = 70
    circumference = 2 * 3.141592653589793 * radius
    circles: list[str] = []
    offset = 0.0
    legend: list[str] = []
    for index, viewpoint in enumerate(rows):
        count = viewpoint.get("comment_count") or 0
        share = count / total
        color = PALETTE[index % len(PALETTE)]
        circles.append(
            f"<circle cx='110' cy='110' r='{radius}' fill='none' stroke='{color}' stroke-width='34' "
            f"stroke-dasharray='{share * circumference:.3f} {circumference:.3f}' "
            f"stroke-dashoffset='{-offset * circumference:.3f}' "
            "transform='rotate(-90 110 110)'></circle>"
        )
        offset += share
        legend.append(
            "<div style='display:flex;align-items:center;gap:6px;font-size:12px;margin:2px 8px'>"
            f"<span style='width:10px;height:10px;border-radius:2px;background:{color};display:inline-block'></span>"
            f"<span>{_esc(viewpoint.get('name'))}（{round(share * 100, 1)}%）</span></div>"
        )
    return (
        "<div class='chart-title'>观点评论占比（Top %d）</div>" % min(top, len(rows))
        + "<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:center'>"
        + "<svg width='220' height='220' viewBox='0 0 220 220'>"
        + "".join(circles)
        + "<text x='110' y='106' text-anchor='middle' font-size='13' fill='#6b7280'>有效评论</text>"
        + "<text x='110' y='128' text-anchor='middle' font-size='16' font-weight='600' fill='#1f2329'>"
        + f"{sum((v.get('comment_count') or 0) for v in viewpoints)}</text>"
        + "</svg>"
        + "<div style='flex:1'>" + "".join(legend) + "</div></div>"
    )


# ----------------------------------------------------------------- 结果视图
def _result_view(results: dict, meta: dict, summary: dict, viewpoints: list[dict], task_title: str) -> str:
    topic = meta.get("post_topic") or "未提供主题（由评论内容判断）"
    rows: list[str] = []
    for index, viewpoint in enumerate(viewpoints, start=1):
        label, css = _stance(viewpoint.get("stance", "unclear"))
        rows.append(
            f"<tr class='viewpoint-row' data-name='{_esc(viewpoint.get('name'))}'>"
            f"<td>{_esc(viewpoint.get('viewpoint_id'))}</td>"
            f"<td>{_esc(viewpoint.get('name'))}</td>"
            f"<td class='num'>{_esc(viewpoint.get('comment_count'))}</td>"
            f"<td class='num'>{_esc(viewpoint.get('percentage'))}%</td>"
            f"<td><span class='tag {css}'>{_esc(label)}</span></td>"
            f"<td><button class='btn' data-viewpoint='{_esc(viewpoint.get('viewpoint_id'))}'>查看</button></td>"
            "</tr>"
        )
    return f"""
<div class='card'>
  <div class='head'>
    <div>
      <p class='section-title'>分析结果</p>
      <p class='muted'>{_esc(task_title)}</p>
      <p class='muted'>讨论主题：{_esc(topic)}</p>
    </div>
    <div class='head-actions'>
      <a class='btn' href='#report'>查看完整报告</a>
      <button class='btn primary' id='export-json'>导出数据 JSON</button>
    </div>
  </div>
</div>
{_summary_cards(summary)}
<div class='card'>
  <div class='charts'>
    <div>{_bar_chart(viewpoints)}</div>
    <div>{_donut_chart(viewpoints)}</div>
  </div>
  <p class='muted'>提示：一条评论可能包含多个观点，因此各观点评论数之和可能超过有效评论数，占比之和也可能超过 100%。</p>
</div>
<div class='card'>
  <p class='section-title'>观点列表（按评论数量排序）</p>
  <p class='muted'>排序仅表示评论数量从高到低，不代表对观点价值的评价。</p>
  <input id='viewpoint-filter' class='btn' style='width:240px;cursor:text' placeholder='按观点名称筛选'>
  <table>
    <thead><tr><th>编号</th><th>观点</th><th class='num'>评论数量</th><th class='num'>占比</th>
    <th>立场</th><th>操作</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</div>
"""


# ----------------------------------------------------------------- 报告视图
def _report_view(results: dict, meta: dict, summary: dict, viewpoints: list[dict], comments: dict[str, dict]) -> str:
    valid_count = summary.get("valid_comment_count") or 0
    topic = meta.get("post_topic") or "未提供主题（由评论内容自行判断讨论话题）"
    generated = meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    parts: list[str] = []
    parts.append("<div class='card'>")
    parts.append("<h1 style='font-size:22px;margin:0 0 8px'>评论观点分析报告</h1>")
    parts.append(f"<p class='muted'>讨论主题：{_esc(topic)}</p>")
    parts.append(
        f"<p class='muted'>数据来源：{_esc(meta.get('source_file', '-'))}　生成时间：{_esc(generated)}</p>"
    )
    parts.append(
        f"<p class='muted'>语义分析模型：{_esc(meta.get('chat_model', '-'))}　"
        f"向量模型：{_esc(meta.get('embedding_model', '-'))}</p>"
    )
    parts.append(
        "<p class='note'>本报告中的评论数量由程序根据评论 ID 统计，LLM 仅用于语义理解、观点归纳与理由总结；"
        "完整结构化数据已内嵌在本文件中。</p>"
    )
    parts.append("</div>")

    parts.append("<div class='card'><h2 class='section-title'>一、数据概况</h2>")
    parts.append(
        "<table><tbody>"
        f"<tr><td>原始评论数量</td><td class='num'>{_esc(summary.get('raw_comment_count', 0))}</td></tr>"
        f"<tr><td>有效评论数量（进入语义分析）</td><td class='num'>{_esc(valid_count)}</td></tr>"
        f"<tr><td>无效评论数量（空 / 纯标点表情 / 广告）</td><td class='num'>{_esc(summary.get('invalid_comment_count', 0))}</td></tr>"
        f"<tr><td>重复评论数量</td><td class='num'>{_esc(summary.get('duplicate_comment_count', 0))}</td></tr>"
        f"<tr><td>与主题无关评论数量</td><td class='num'>{_esc(summary.get('irrelevant_comment_count', 0))}</td></tr>"
        f"<tr><td>相关但无法识别明确观点数量</td><td class='num'>{_esc(summary.get('unclear_comment_count', 0))}</td></tr>"
        f"<tr><td>表达出至少一个观点的评论数量</td><td class='num'>{_esc(summary.get('comment_with_viewpoint_count', 0))}</td></tr>"
        f"<tr><td>包含多个观点的评论数量</td><td class='num'>{_esc(summary.get('multi_viewpoint_comment_count', 0))}</td></tr>"
        f"<tr><td>最终观点数量</td><td class='num'>{_esc(summary.get('viewpoint_count', len(viewpoints)))}</td></tr>"
        f"<tr><td>观点-评论关联总数（去重后合计）</td><td class='num'>{_esc(summary.get('viewpoint_comment_link_count', 0))}</td></tr>"
        f"<tr><td>平均每条有效评论表达观点数</td><td class='num'>{_esc(summary.get('avg_viewpoints_per_valid_comment', 0))}</td></tr>"
        "</tbody></table>"
    )
    parts.append(
        "<p class='note'>统计口径：观点人数 = 该观点下去重的 comment_id 数量；"
        "观点占比 = 观点评论数 ÷ 有效评论数；理由占比 = 理由评论数 ÷ 该观点评论数。</p></div>"
    )

    parts.append("<div class='card'><h2 class='section-title'>二、核心观点统计</h2>")
    parts.append(
        "<table><thead><tr><th class='num'>排名</th><th>观点</th><th class='num'>评论数</th>"
        "<th class='num'>占有效评论比例</th><th>立场</th></tr></thead><tbody>"
    )
    for index, viewpoint in enumerate(viewpoints, start=1):
        label, _css = _stance(viewpoint.get("stance", "unclear"))
        parts.append(
            "<tr>"
            f"<td class='num'>{index}</td><td>{_esc(viewpoint.get('name'))}</td>"
            f"<td class='num'>{_esc(viewpoint.get('comment_count'))}</td>"
            f"<td class='num'>{_esc(viewpoint.get('percentage'))}%</td><td>{_esc(label)}</td></tr>"
        )
    total_links = sum((v.get("comment_count") or 0) for v in viewpoints)
    total_share = round(total_links / valid_count * 100, 1) if valid_count else 0
    parts.append(
        f"<tr><td class='num'>合计</td><td>观点-评论关联（含一条评论多个观点）</td>"
        f"<td class='num'>{total_links}</td><td class='num'>{total_share}%</td><td>—</td></tr></tbody></table>"
    )
    parts.append("<p class='muted'>表中的“排名”仅表示评论数量从高到低的展示顺序，不代表对观点价值的评价。</p></div>")

    parts.append("<div class='card'><h2 class='section-title'>三、观点详细分析</h2>")
    for viewpoint in viewpoints:
        label, css = _stance(viewpoint.get("stance", "unclear"))
        parts.append("<div style='border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:12px 0'>")
        parts.append(
            "<div class='head'><div style='font-size:15px;font-weight:600'>"
            f"{_esc(viewpoint.get('viewpoint_id'))}　{_esc(viewpoint.get('name'))}</div>"
            f"<div><span class='tag {css}'>{_esc(label)}</span>　<b>{_esc(viewpoint.get('comment_count'))}</b> 条"
            f"（{_esc(viewpoint.get('percentage'))}%）</div></div>"
        )
        if viewpoint.get("description"):
            parts.append(f"<p class='muted'>{_esc(viewpoint['description'])}</p>")
        reasons = viewpoint.get("reasons") or []
        parts.append("<p style='font-weight:600;margin:10px 0 4px'>主要理由</p>")
        if reasons:
            parts.append(
                "<table><thead><tr><th>理由</th><th class='num'>评论数</th>"
                "<th class='num'>占该观点比例</th></tr></thead><tbody>"
            )
            for reason in reasons:
                parts.append(
                    f"<tr><td>{_esc(reason.get('name'))}</td>"
                    f"<td class='num'>{_esc(reason.get('comment_count'))}</td>"
                    f"<td class='num'>{_esc(reason.get('percentage'))}%</td></tr>"
                )
            parts.append("</tbody></table>")
        else:
            parts.append("<p class='muted'>评论中未给出明确理由。</p>")
        representative = viewpoint.get("representative_comments") or []
        parts.append("<p style='font-weight:600;margin:10px 0 4px'>代表性评论</p>")
        if representative:
            parts.extend(f"<blockquote>{_esc(text)}</blockquote>" for text in representative)
        else:
            parts.append("<p class='muted'>暂无代表性评论。</p>")
        comment_ids = viewpoint.get("comment_ids") or []
        if comment_ids and comments:
            shown = comment_ids[:50]
            parts.append(
                f"<details><summary>展开该观点对应的原始评论（共 {len(comment_ids)} 条，此处列出前 {len(shown)} 条）</summary>"
                "<table><thead><tr><th>comment_id</th><th>原始评论</th><th class='num'>点赞</th></tr></thead><tbody>"
            )
            for comment_id in shown:
                item = comments.get(comment_id, {})
                parts.append(
                    f"<tr><td><code>{_esc(comment_id)}</code></td>"
                    f"<td>{_esc(item.get('content', '（该评论不在清洗结果中）'))}</td>"
                    f"<td class='num'>{_esc(item.get('like_count', 0))}</td></tr>"
                )
            parts.append("</tbody></table></details>")
        parts.append("</div>")
    parts.append("</div>")

    parts.append("<div class='card'><h2 class='section-title'>四、无法明确判断的评论</h2>")
    parts.append(
        f"<p class='muted'>相关但没有明确观点：{_esc(summary.get('unclear_comment_count', 0))} 条；"
        f"与主题无关：{_esc(summary.get('irrelevant_comment_count', 0))} 条。</p>"
    )
    for title_text, key in (
        ("没有明确观点的评论示例", "unclear_comment_examples"),
        ("与主题无关的评论示例", "irrelevant_comment_examples"),
    ):
        examples = results.get(key) or []
        if not examples:
            continue
        parts.append(f"<p style='font-weight:600;margin:10px 0 4px'>{_esc(title_text)}</p>")
        parts.extend(f"<blockquote>{_esc(example.get('content'))}</blockquote>" for example in examples[:10])
    parts.append("</div>")

    parts.append("<div class='card'><h2 class='section-title'>五、分析方法说明</h2>")
    parts.append(
        "<pre>评论 JSON\n"
        "  ↓ 数据读取 + 清洗/去重（Python）\n"
        "  ↓ LLM 批量语义提取：每条评论 → 0..n 个观点（statement / stance / reason）\n"
        "  ↓ Embedding 生成语义向量（本地 bge-base-zh-v1.5）\n"
        "  ↓ 余弦相似度聚类\n"
        "  ↓ LLM 归并 / 命名最终观点（允许合并与拆分）\n"
        "  ↓ 程序绑定 viewpoint_id 并统计人数、占比（Python）\n"
        "  ↓ LLM 对每个观点的理由归纳 + 程序统计理由人数\n"
        "  ↓ 生成 analysis_results.json 与 analysis_report.md / .html</pre>"
    )
    parts.append("<p class='note'>本报告中的评论数量由程序根据评论 ID 进行统计，LLM 仅用于语义理解、观点归纳和理由总结。</p>")
    parts.append("</div>")
    return "".join(parts)


def _drawer_html() -> str:
    return """
<div class='drawer-mask' id='drawer-mask'></div>
<aside class='drawer' id='drawer' aria-label='观点详情'>
  <div class='drawer-head'>
    <span class='drawer-title' id='drawer-title'>观点详情</span>
    <button class='btn' id='drawer-close'>关闭</button>
  </div>
  <div class='drawer-body'>
    <div class='card' id='drawer-summary'></div>
    <div class='card'><p class='section-title'>主要理由</p><div id='drawer-reasons'></div></div>
    <div class='card'><p class='section-title'>代表性评论</p><div id='drawer-representative'></div></div>
    <div class='card'>
      <p class='section-title'>该观点对应的原始评论</p>
      <div id='drawer-comments'></div>
      <div class='pager' id='drawer-pager'></div>
    </div>
  </div>
</aside>
"""


def build_html_report(
    results: dict,
    *,
    comments: dict[str, dict] | None = None,
    task_title: str | None = None,
    mode: str = "standalone",
    generated_at: str | None = None,
) -> str:
    """生成 HTML 报告。

    results:  analysis_results.json 内容
    comments: comment_id -> {comment_id, content, like_count}
    mode:     "standalone"（下载用完整页面：分析结果 + 完整报告）
              "report"（仅报告正文，供应用内 iframe 使用）
    """
    comments = comments or {}
    summary = results.get("summary", {}) or {}
    meta = dict(results.get("meta", {}) or {})
    if generated_at:
        meta["generated_at"] = generated_at
    viewpoints = results.get("viewpoints", []) or []
    title = task_title or "评论观点智能分析"

    report_body = _report_view(results, meta, summary, viewpoints, comments)

    if mode == "report":
        return (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_esc(title)}</title><style>{STYLE}</style></head>"
            "<body><div class='page'>" + report_body + "</div></body></html>"
        )

    embedded = {
        "meta": meta,
        "summary": summary,
        "integrity": results.get("integrity", {}),
        "viewpoints": viewpoints,
        "unclear_comment_examples": results.get("unclear_comment_examples", []),
        "irrelevant_comment_examples": results.get("irrelevant_comment_examples", []),
        "comment_index": comments,
    }
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{STYLE}</style></head><body>"
        "<header class='app-header'><div class='header-inner'>"
        "<span class='brand'>评论观点智能分析</span>"
        "<nav class='nav'>"
        "<a href='#result' class='active'>分析结果</a>"
        "<a href='#report'>完整报告</a>"
        "</nav>"
        "<span class='tagline'>这是离线存档：数据已内嵌在本文件中，可直接分享或归档</span>"
        "</div></header>"
        "<div class='page'>"
        f"<section class='view active' id='view-result'>{_result_view(results, meta, summary, viewpoints, title)}</section>"
        f"<section class='view' id='view-report'>{report_body}</section>"
        "<footer>由「评论观点智能分析」生成 · 分析与网页展示结构一致，可离线查看</footer>"
        "</div>"
        + _drawer_html()
        + _json_script(embedded)
        + f"<script>{SCRIPT.replace('__PAGE_SIZE__', str(COMMENT_PAGE_SIZE))}</script>"
        + "</body></html>"
    )
