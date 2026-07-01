/* 監査レビューコンソール — データ駆動のレンダリング。window.DEMO_DATA を読み各画面を描画する。
   すべて合成データ。所見はAIの提示であり確定ではない（確定は人間＝HITL）。 */
(function () {
  "use strict";
  const D = window.DEMO_DATA || {};
  const SEV = ["critical", "high", "medium", "low"];
  const SEV_JA = { critical: "重大", high: "高", medium: "中", low: "低" };
  const SEV_COLOR = { critical: "#b42318", high: "#c2410c", medium: "#a16207", low: "#475467" };
  const ROLE_JA = { "rule_engine": "ルール", "ml_model": "ML", "agent": "エージェント", "system": "システム" };

  // ---- ユーティリティ ----
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const yen = (n) => "¥" + Math.round(Number(n || 0)).toLocaleString("ja-JP");
  const num = (n) => Number(n || 0).toLocaleString("ja-JP");
  const pct = (n, d = 1) => (Number(n || 0) * 100).toFixed(d) + "%";
  const shortHash = (h) => h ? (h.slice(0, 8) + "…") : "—";
  const assertName = (a) => (D.assertion_names && D.assertion_names[a]) || a;
  const catName = (c) => (D.category_names && D.category_names[c]) || c;
  const ruleName = (id) => (D.rules && D.rules[id] && D.rules[id].name_ja) || id;

  function h(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === "class") e.className = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (k.startsWith("on") && typeof attrs[k] === "function") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    }
    if (children != null) (Array.isArray(children) ? children : [children]).forEach(c => {
      if (c == null) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }
  function sevBadge(sev) {
    if (!sev) return `<span class="badge sev-low"><span class="dot"></span>—</span>`;
    return `<span class="badge sev-${sev}"><span class="dot"></span>${SEV_JA[sev] || sev}</span>`;
  }
  function scorePill(score) {
    const s = Math.round(Number(score || 0));
    const col = s >= 70 ? "#b42318" : s >= 40 ? "#c2410c" : "#667085";
    return `<span class="score-pill"><span class="score-bar"><span style="width:${s}%;background:${col}"></span></span><span style="color:${col}">${s}</span></span>`;
  }

  // ---- SVGチャート ----
  function donut(segments, size = 132) {
    const total = segments.reduce((a, s) => a + s.value, 0) || 1;
    const r = size / 2 - 12, cx = size / 2, cy = size / 2, C = 2 * Math.PI * r;
    let off = 0;
    const rings = segments.filter(s => s.value > 0).map(s => {
      const len = (s.value / total) * C;
      const el = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color}" stroke-width="18"
        stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 ${cx} ${cy})" stroke-linecap="butt"/>`;
      off += len; return el;
    }).join("");
    const sel = segments.reduce((a, s) => a + s.value, 0);
    return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="重要度別内訳">
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#eef1f4" stroke-width="18"/>${rings}
      <text x="${cx}" y="${cy - 3}" text-anchor="middle" font-size="26" font-weight="720" fill="#10192a">${sel}</text>
      <text x="${cx}" y="${cy + 15}" text-anchor="middle" font-size="10.5" fill="#667085">所見</text></svg>`;
  }
  function hbars(items, opts) {
    opts = opts || {};
    const max = Math.max(1, ...items.map(i => i.value));
    const rowH = 30, w = opts.width || 460, labelW = opts.labelW || 150, barW = w - labelW - 54;
    const svgH = items.length * rowH + 6;
    const rows = items.map((it, i) => {
      const y = i * rowH + 4, bw = Math.max(2, (it.value / max) * barW);
      return `<g>
        <text x="0" y="${y + 15}" font-size="12" fill="#384358">${esc(it.label)}</text>
        <rect x="${labelW}" y="${y + 5}" width="${barW}" height="14" rx="4" fill="#f2f4f7"/>
        <rect x="${labelW}" y="${y + 5}" width="${bw}" height="14" rx="4" fill="${it.color || "#0e7c7b"}"/>
        <text x="${labelW + barW + 8}" y="${y + 16}" font-size="12" font-weight="600" fill="#10192a">${num(it.value)}</text></g>`;
    }).join("");
    return `<svg class="chart" viewBox="0 0 ${w} ${svgH}" width="100%" height="${svgH}" role="img">${rows}</svg>`;
  }
  function funnelChart(total, deterministic, selected) {
    const w = 540, h = 158, cx = w / 2, maxW = 470;
    // 幅は段階的に固定（視覚的なファネル）。件数はラベルに実数で表示。
    const stages = [
      { label: `全取引（母集団）：${num(total)} 件`, ratio: 1.0, color: "#c7d0dd", fg: "#0c1523" },
      { label: `決定論的スコアリング：${num(total)} 件`, ratio: 0.66, color: "#7fb5b2", fg: "#08302f" },
      { label: `高リスク選別：${num(selected)} 件`, ratio: 0.4, color: "#0e7c7b", fg: "#ffffff" },
    ];
    let y = 12;
    const parts = stages.map(s => {
      const bw = maxW * s.ratio, x = cx - bw / 2, bh = 36;
      const seg = `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="7" fill="${s.color}"/>
        <text x="${cx}" y="${y + 23}" text-anchor="middle" font-size="12.5" font-weight="650" fill="${s.fg}">${esc(s.label)}</text>`;
      y += bh + 11; return seg;
    }).join("");
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" height="auto" role="img" aria-label="ファネル：全取引から高リスク選別へ">${parts}</svg>`;
  }

  // ---- 極小 Markdown（レポート用サブセット）----
  function mdToHtml(md) {
    const lines = String(md || "").split("\n");
    let out = "", inList = false, tbl = [];
    const flushList = () => { if (inList) { out += "</ul>"; inList = false; } };
    const flushTable = () => {
      if (!tbl.length) return;
      const rows = tbl.filter(r => !/^\|[\s\-:|]+\|$/.test(r.trim()));
      const cells = rows.map(r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim()));
      out += "<table>" + cells.map((c, i) =>
        "<tr>" + c.map(x => `<${i === 0 ? "th" : "td"}>${inline(x)}</${i === 0 ? "th" : "td"}>`).join("") + "</tr>").join("") + "</table>";
      tbl = [];
    };
    const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>");
    for (let raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (line.trim().startsWith("|")) { flushList(); tbl.push(line); continue; }
      flushTable();
      if (/^#{1,6}\s/.test(line)) { flushList(); const lvl = line.match(/^#+/)[0].length; out += `<h${lvl}>${inline(line.replace(/^#+\s/, ""))}</h${lvl}>`; }
      else if (/^>\s?/.test(line)) { flushList(); out += `<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`; }
      else if (/^[-*]\s/.test(line)) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(line.replace(/^[-*]\s/, ""))}</li>`; }
      else if (line.trim() === "") { flushList(); }
      else { flushList(); out += `<p>${inline(line)}</p>`; }
    }
    flushList(); flushTable();
    return out;
  }

  // ============================ 画面 ============================
  function renderDashboard() {
    const pop = D.population, fn = D.funnel, bd = D.breakdown, dq = D.data_quality, ex = D.exploratory, ag = D.agent;
    const sevSeg = SEV.map(s => ({ label: SEV_JA[s], value: (bd.by_severity[s] || 0), color: SEV_COLOR[s] })).filter(s => s.value);
    const assertItems = Object.entries(bd.by_assertion || {}).sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: assertName(k), value: v, color: "#0e7c7b" }));
    const catCounts = {};
    (D.findings || []).forEach(f => (f.rule_ids || []).forEach(r => { const c = r.split("-")[0]; catCounts[c] = (catCounts[c] || 0) + 1; }));
    const catItems = Object.entries(catCounts).sort((a, b) => b[1] - a[1]).slice(0, 8)
      .map(([k, v]) => ({ label: catName(k), value: v, color: "#5a6b7f" }));
    const recon = dq.reconciled_to_gl;

    const kpis = [
      { label: "評価対象取引（全件）", value: num(pop.transaction_count), unit: "件", sub: `${(pop.period_coverage || []).length} 会計期間 / 4 法人`, icon: iconDb },
      { label: "高リスク所見（選別）", value: num(fn.selected), unit: "件", sub: `全件の ${pct(fn.selection_rate, 2)} を深掘り対象に絞り込み`, icon: iconFilter },
      { label: "うち重大（critical）", value: num(bd.by_severity.critical || 0), unit: "件", sub: `高 ${num(bd.by_severity.high || 0)} 件 / 中 ${num(bd.by_severity.medium || 0)} 件`, icon: iconAlert },
      { label: "総勘定元帳との突合", value: recon ? "一致" : "要確認", unit: "", sub: recon ? "母集団の網羅性を裏付け" : "GL情報を確認", icon: iconCheck, ok: recon },
    ];

    const view = h("div", { class: "grid" });
    // KPI
    const cards = h("div", { class: "cards", "data-demo": "kpis" });
    kpis.forEach(k => cards.appendChild(h("div", {
      class: "card kpi", html: `
      <div class="icon">${k.icon}</div>
      <h3>${esc(k.label)}</h3>
      <div class="value" style="${k.ok ? "color:#067647" : ""}">${k.value}${k.unit ? `<span class="unit">${k.unit}</span>` : ""}</div>
      <div class="delta">${esc(k.sub)}</div>` })));
    view.appendChild(cards);

    // ファネル + 重要度ドーナツ
    const row1 = h("div", { class: "grid", style: "grid-template-columns: 1.5fr 1fr;" });
    row1.appendChild(h("div", { class: "card pad-lg", "data-demo": "funnel", html: `
      <div class="section-title" style="margin:0 0 8px"><h2>コスト・ファネル（全件 → 高リスク）</h2></div>
      <p class="small muted" style="margin:0 0 6px">全件を決定論的（ルール／ML／ネットワーク）に低コストで評価し、高リスク部分集合のみをエージェントが深掘りします。</p>
      ${funnelChart(fn.total, fn.total, fn.selected)}` }));
    const donutCard = h("div", { class: "card", html: `
      <div class="section-title" style="margin:0 0 6px"><h2>重要度別</h2></div>
      <div style="display:flex;gap:16px;align-items:center;justify-content:center;flex-wrap:wrap">
        <div class="chart">${donut(sevSeg)}</div>
        <div class="legend" style="flex-direction:column;gap:8px">
          ${SEV.map(s => bd.by_severity[s] ? `<span><i style="background:${SEV_COLOR[s]}"></i>${SEV_JA[s]}：${num(bd.by_severity[s])} 件</span>` : "").join("")}
        </div>
      </div>` });
    row1.appendChild(donutCard);
    view.appendChild(row1);

    // アサーション + カテゴリ
    const row2 = h("div", { class: "grid", style: "grid-template-columns: 1fr 1fr;" });
    row2.appendChild(h("div", { class: "card", "data-demo": "assertions", html: `
      <div class="section-title" style="margin:0 0 10px"><h2>財務諸表アサーション別</h2><span class="hint">監査人が検証すべき論点に紐づけ</span></div>
      ${hbars(assertItems, { labelW: 130 })}` }));
    row2.appendChild(h("div", { class: "card", html: `
      <div class="section-title" style="margin:0 0 10px"><h2>リスクカテゴリ別（上位）</h2></div>
      ${hbars(catItems, { labelW: 150 })}` }));
    view.appendChild(row2);

    // データ品質 + エージェント + 検証
    const row3 = h("div", { class: "grid", style: "grid-template-columns: 1fr 1fr 1fr;" });
    row3.appendChild(h("div", { class: "card", "data-demo": "dq", html: `
      <div class="section-title" style="margin:0 0 10px"><h2>データ品質（全件主張の裏付け）</h2></div>
      <dl class="kv">
        <dt>GL突合</dt><dd>${recon ? `<span class="badge pill-ok"><span class="dot"></span>一致</span>` : `<span class="badge pill-warn">要確認</span>`}</dd>
        <dt>連番の欠番</dt><dd>${num(dq.sequence_gaps.length)} 件</dd>
        <dt>連番の重複</dt><dd>${num(dq.sequence_duplicates.length)} 件</dd>
        <dt>期間網羅</dt><dd>${(pop.period_coverage || []).length} 期間（欠落 ${num((dq.missing_periods || []).length)}）</dd>
        <dt>スキーマ不適合</dt><dd>${num(dq.invalid_count)} 件</dd>
      </dl>` }));
    row3.appendChild(h("div", { class: "card", html: `
      <div class="section-title" style="margin:0 0 10px"><h2>エージェント探索（read-only）</h2></div>
      <dl class="kv">
        <dt>証憑ツール呼出</dt><dd>${num(ag.tool_calls)} 回</dd>
        <dt>HITLゲート抑止</dt><dd>${num(ag.gated_skips)} 回</dd>
        <dt>インジェクション検出</dt><dd>${ag.injections_detected ? `<span class="badge pill-warn">${num(ag.injections_detected)} 件</span>` : "0 件"}</dd>
        <dt>証憑と一次データの矛盾</dt><dd>${ag.contradictions_detected ? `<span class="badge pill-warn">${num(ag.contradictions_detected)} 件</span>` : "0 件"}</dd>
      </dl>` }));
    const detected = (D.scenarios_detected || []);
    const nDet = detected.filter(d => d.detected).length;
    row3.appendChild(h("div", { class: "card", html: `
      <div class="section-title" style="margin:0 0 10px"><h2>モデル検証（合成データ）</h2><span class="hint">検証目的</span></div>
      <div class="value" style="font-size:26px;font-weight:720">${nDet} / ${detected.length}</div>
      <p class="small muted" style="margin:4px 0 0">本デモに混入した合成不正シナリオの検出状況です。実運用での検出性能を保証・示唆するものではありません。</p>` }));
    view.appendChild(row3);

    // 注記
    view.appendChild(h("div", { class: "callout info", style: "margin-top:6px", html: `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex:none"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
      <div>${esc(D.meta.disclaimer_ja)}</div>` }));
    return view;
  }

  // 所見一覧
  const state = { sev: new Set(["critical", "high"]), selectedOnly: false, cat: "", q: "", sort: "risk" };
  function renderFindings() {
    const view = h("div");
    const total = D.findings.length;
    const bySev = D.breakdown.by_severity;

    // ツールバー
    const toolbar = h("div", { class: "toolbar", "data-demo": "filters" });
    SEV.filter(s => s !== "low").forEach(s => {
      const b = h("button", { class: `filter-chip ${s === "critical" ? "crit" : s === "high" ? "high" : "med"}`, "aria-pressed": state.sev.has(s) },
        `${SEV_JA[s]}（${num(bySev[s] || 0)}）`);
      b.addEventListener("click", () => { state.sev.has(s) ? state.sev.delete(s) : state.sev.add(s); draw(); });
      toolbar.appendChild(b);
    });
    const selBtn = h("button", { class: "filter-chip", "aria-pressed": state.selectedOnly }, "選別済みのみ");
    selBtn.addEventListener("click", () => { state.selectedOnly = !state.selectedOnly; draw(); });
    toolbar.appendChild(selBtn);
    const catSel = h("select", { "aria-label": "カテゴリで絞り込み", onchange: (e) => { state.cat = e.target.value; draw(); } },
      [h("option", { value: "" }, "全カテゴリ")].concat(Object.keys(D.category_names).map(c => h("option", { value: c }, catName(c)))));
    catSel.value = state.cat;
    toolbar.appendChild(catSel);
    const search = h("input", { type: "search", placeholder: "ID・要約・ルールで検索", value: state.q, oninput: (e) => { state.q = e.target.value; draw(); } });
    toolbar.appendChild(search);
    toolbar.appendChild(h("span", { class: "spacer", style: "flex:1" }));
    const countEl = h("span", { class: "small muted" });
    toolbar.appendChild(countEl);
    view.appendChild(toolbar);

    const wrap = h("div", { class: "table-wrap" });
    view.appendChild(wrap);

    function filtered() {
      const q = state.q.trim().toLowerCase();
      return D.findings.filter(f => {
        if (state.sev.size && !state.sev.has(f.severity)) return false;
        if (state.selectedOnly && !f.selected_for_deepdive) return false;
        if (state.cat && !(f.rule_ids || []).some(r => r.startsWith(state.cat))) return false;
        if (q) {
          const hay = (f.finding_id + " " + (f.rationale && f.rationale.summary_ja || "") + " " + (f.rule_ids || []).join(" ")).toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      }).sort((a, b) => b.risk_score - a.risk_score);
    }
    function draw() {
      toolbar.querySelectorAll(".filter-chip").forEach(() => { });
      SEV.filter(s => s !== "low").forEach((s, i) => { const b = toolbar.children[i]; if (b) b.setAttribute("aria-pressed", state.sev.has(s)); });
      selBtn.setAttribute("aria-pressed", state.selectedOnly);
      const rows = filtered();
      countEl.textContent = `${num(rows.length)} 件を表示（全 ${num(total)} 件）`;
      const table = h("table", { class: "data" });
      table.innerHTML = `<thead><tr>
        <th>所見ID</th><th>重要度</th><th class="num">リスクスコア</th><th>アサーション</th>
        <th>発火ルール</th><th>生成</th><th>HITL状態</th><th>選別</th></tr></thead>`;
      const tbody = h("tbody");
      rows.slice(0, 400).forEach(f => {
        const tr = h("tr", { tabindex: "0", role: "button", "aria-label": `${f.finding_id} を開く` });
        tr.innerHTML = `
          <td class="mono">${esc(f.finding_id)}</td>
          <td>${sevBadge(f.severity)}</td>
          <td class="num">${scorePill(f.risk_score)}</td>
          <td>${(f.assertion || []).slice(0, 3).map(a => `<span class="chip assertion">${esc(assertName(a))}</span>`).join(" ") || "—"}</td>
          <td>${(f.rule_ids || []).slice(0, 3).map(r => `<span class="chip rule">${esc(r)}</span>`).join(" ")}${(f.rule_ids || []).length > 3 ? ` <span class="small muted">+${f.rule_ids.length - 3}</span>` : ""}</td>
          <td><span class="small muted">${ROLE_JA[f.created_by] || f.created_by}</span></td>
          <td>${hitlBadge(f.hitl_status)}</td>
          <td>${f.selected_for_deepdive ? `<span class="badge pill-ok"><span class="dot"></span>深掘り</span>` : `<span class="small muted">—</span>`}</td>`;
        const open = () => navigate("console", f.finding_id);
        tr.addEventListener("click", open);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      wrap.innerHTML = "";
      wrap.appendChild(table);
      if (!rows.length) wrap.appendChild(h("div", { class: "small muted", style: "padding:22px;text-align:center" }, "条件に一致する所見はありません。フィルタを調整してください。"));
    }
    draw();
    return view;
  }
  function hitlBadge(s) {
    const map = { open: ["未着手", "sev-low"], in_review: ["レビュー中", "sev-medium"], confirmed: ["確定(人間)", "sev-high"], dismissed: ["棄却(人間)", "sev-low"] };
    const [t, c] = map[s] || [s, "sev-low"];
    return `<span class="badge ${c}">${t}</span>`;
  }

  // レビューコンソール（HITL）
  function renderConsole(findingId) {
    const f = D.findings.find(x => x.finding_id === findingId) || D.findings[0];
    const tid = (f.transaction_ids || [])[0];
    const t = (D.transactions || {})[tid] || {};
    const ev = (D.evidence || []).filter(e => e.finding_id === f.finding_id);
    const view = h("div");

    // ヘッダ
    view.appendChild(h("div", { class: "card pad-lg", style: "margin-bottom:16px", html: `
      <div class="detail-head">
        <div class="idblock">
          <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
            <span class="mono" style="font-size:15px;font-weight:700">${esc(f.finding_id)}</span>
            ${sevBadge(f.severity)} ${hitlBadge(f.hitl_status)}
            ${f.selected_for_deepdive ? `<span class="badge pill-ok"><span class="dot"></span>深掘り対象</span>` : ""}
          </div>
          <p style="margin:8px 0 0;color:#384358">${esc(f.rationale && f.rationale.summary_ja || "")}</p>
        </div>
        <div style="text-align:right">
          <div class="small muted">リスクスコア</div>
          <div style="font-size:30px;font-weight:730;color:${f.risk_score >= 70 ? "#b42318" : "#c2410c"}">${Math.round(f.risk_score)}</div>
          <div class="small muted">生成: ${ROLE_JA[f.created_by] || f.created_by}</div>
        </div>
      </div>` }));

    const grid = h("div", { class: "detail-grid" });

    // 左: 取引の事実 + カットオフ・タイムライン + ルール
    const left = h("div", { class: "grid" });
    left.appendChild(h("div", { class: "card", html: `
      <div class="section-title" style="margin:0 0 10px"><h2>取引の事実</h2></div>
      <dl class="kv">
        <dt>取引ID</dt><dd class="mono">${esc(tid)}</dd>
        <dt>計上法人</dt><dd>${esc(t.entity_id || "—")}</dd>
        <dt>得意先</dt><dd>${esc(t.customer_name || t.customer_id || "—")}</dd>
        <dt>会計期間</dt><dd>${esc(t.period || "—")}</dd>
        <dt>金額</dt><dd style="font-weight:600">${yen(t.amount)}</dd>
        <dt>チャネル</dt><dd>${esc(t.channel || "—")}</dd>
        <dt>製品</dt><dd>${esc(t.product_name || t.product_id || "—")}</dd>
        <dt>計上区分</dt><dd>${esc(t.source_system || "—")}${t.poster_role ? `（起票: ${esc(t.poster_role)}）` : ""}</dd>
      </dl>` }));
    left.appendChild(h("div", { class: "card", "data-demo": "timeline", html: `
      <div class="section-title" style="margin:0 0 6px"><h2>日付の三角照合（カットオフ）</h2><span class="hint">受注→出荷→検収→計上の整合</span></div>
      ${timeline(t)}` }));
    left.appendChild(h("div", { class: "card", html: `
      <div class="section-title" style="margin:0 0 10px"><h2>発火ルールとアサーション</h2></div>
      ${(f.rule_ids || []).map(rid => {
      const r = (D.rules || {})[rid] || {};
      return `<div style="padding:9px 0;border-bottom:1px solid #eef1f4">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <span class="chip rule">${esc(rid)}</span> <strong style="font-size:13px">${esc(r.name_ja || "")}</strong> ${sevBadge(r.severity)}
          </div>
          <div style="margin-top:5px">${(r.assertion || []).map(a => `<span class="chip assertion">${esc(assertName(a))}</span>`).join(" ")}</div>
        </div>`;
    }).join("") || `<p class="small muted">ルール発火なし（ML由来）</p>`}` }));
    grid.appendChild(left);

    // 右: ML寄与 + 仮説 + 証憑 + 推奨 + HITL
    const right = h("div", { class: "grid" });
    if (f.ml_scores && f.ml_scores.shap_top && f.ml_scores.shap_top.length) {
      const items = f.ml_scores.shap_top.map(s => ({ label: featJa(s.feature), value: Math.round(s.contribution * 100) / 100, color: "#6941c6" }));
      right.appendChild(h("div", { class: "card", html: `
        <div class="section-title" style="margin:0 0 8px"><h2>ML異常スコアと寄与要因</h2><span class="hint">説明可能性（SHAP）</span></div>
        <div class="small muted" style="margin-bottom:6px">異常スコア <strong style="color:#6941c6">${Math.round(f.ml_scores.anomaly_score)}</strong> / 100 ・ モデル ${esc(f.ml_scores.model_id || "")}</div>
        ${hbars(items, { labelW: 160 })}` }));
    }
    if (f.hypothesis_ja) right.appendChild(h("div", { class: "card", html: `
      <div class="section-title" style="margin:0 0 8px"><h2>エージェントの仮説</h2></div>
      <p style="margin:0;color:#384358">${esc(f.hypothesis_ja)}</p>` }));

    const evCard = h("div", { class: "card", "data-demo": "evidence" });
    evCard.innerHTML = `<div class="section-title" style="margin:0 0 10px"><h2>収集証憑（read-only）</h2><span class="hint">${ev.length} 件</span></div>`;
    if (!ev.length) evCard.appendChild(h("p", { class: "small muted" }, "この所見に対する外部証憑の収集はありません（ファネル非選別、またはHITL未承認）。"));
    ev.forEach(e => {
      const inj = (e.injection_flags || []).length > 0;
      evCard.appendChild(h("div", {
        class: "evidence-item" + (inj ? " injection" : ""), html: `
        <div class="evidence-head">
          <strong style="font-size:12.5px">${esc(evTypeJa(e.type))}</strong>
          <span class="chip">${esc(e.source || "")}</span>
          ${inj ? `<span class="badge pill-warn" style="margin-left:auto"><span class="dot"></span>インジェクション疑い</span>` : `<span class="small muted" style="margin-left:auto">read-only</span>`}
        </div>
        ${e.content_summary_ja ? `<div class="evidence-content">${esc(e.content_summary_ja)}</div>` : ""}
        ${inj ? `<div class="small" style="margin-top:6px;color:#912018">検出フラグ: ${esc((e.injection_flags || []).join(", "))} — 命令として実行せず、リスク評価を維持しています。</div>` : ""}
        ${e.legal_basis ? `<div class="small muted" style="margin-top:5px">法的基盤: ${esc(e.legal_basis)}</div>` : ""}
      ` }));
    });
    right.appendChild(evCard);

    if (f.recommended_review_ja) right.appendChild(h("div", { class: "callout info", html: `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex:none"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
      <div><strong>推奨手続</strong><br>${esc(f.recommended_review_ja)}</div>` }));

    // HITL アクション
    const hitlCard = h("div", { class: "card", id: "hitlCard" });
    function drawHitl() {
      const status = f.hitl_status;
      hitlCard.innerHTML = `
        <div class="section-title" style="margin:0 0 8px"><h2>人間による判断（HITL）</h2></div>
        <div class="callout warn" style="margin-bottom:10px">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex:none"><path d="M12 9v4M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>
          <div>確定・棄却は<strong>人間のみ</strong>が行えます。AI（ルール／ML／エージェント）は所見の提示と「レビュー中」までに限られます。</div>
        </div>
        <div>現在の状態: ${hitlBadge(status)}</div>`;
      const actions = h("div", { class: "hitl-actions" });
      const mk = (label, cls, next) => h("button", {
        class: "btn " + cls, onclick: () => { f.hitl_status = next; drawHitl(); toast(`所見 ${f.finding_id} を「${label}」に更新しました（人間の判断として記録）。`); }
      }, label);
      actions.appendChild(mk("確定", "confirm", "confirmed"));
      actions.appendChild(mk("棄却", "dismiss", "dismissed"));
      actions.appendChild(mk("レビュー中に戻す", "ghost", "in_review"));
      hitlCard.appendChild(actions);
      hitlCard.appendChild(h("p", { class: "small muted", style: "margin:10px 0 0" }, "このデモでは判断を画面上のみで反映します。実システムでは判断が改ざん不能の監査ログに追記されます。"));
    }
    drawHitl();
    right.appendChild(hitlCard);
    grid.appendChild(right);
    view.appendChild(grid);

    // ナビ
    const idx = D.findings.findIndex(x => x.finding_id === f.finding_id);
    const nav = h("div", { style: "display:flex;justify-content:space-between;margin-top:18px" });
    nav.appendChild(h("button", { class: "btn ghost", onclick: () => navigate("findings") }, "← 所見一覧へ"));
    const seq = h("div", { style: "display:flex;gap:8px" });
    seq.appendChild(h("button", { class: "btn sm", disabled: idx <= 0 ? "" : null, onclick: () => idx > 0 && navigate("console", D.findings[idx - 1].finding_id) }, "前の所見"));
    seq.appendChild(h("button", { class: "btn sm", disabled: idx >= D.findings.length - 1 ? "" : null, onclick: () => idx < D.findings.length - 1 && navigate("console", D.findings[idx + 1].finding_id) }, "次の所見"));
    nav.appendChild(seq);
    view.appendChild(nav);
    return view;
  }
  function timeline(t) {
    const rec = t.revenue_recognition_date;
    const steps = [
      ["受注", t.order_date], ["出荷", t.ship_date], ["検収・納品", t.delivery_date],
      ["請求", t.invoice_date], ["収益認識", rec], ["取消・返品", t.reversal_date],
    ].filter(s => s[1]);
    const recD = rec ? Date.parse(rec) : null;
    return `<div class="timeline">` + steps.map(([label, d]) => {
      let flag = false;
      if (recD) {
        if (label === "出荷" && Date.parse(d) > recD) flag = true;       // 未出荷計上
        if (label === "検収・納品" && Date.parse(d) > recD) flag = true;  // 検収前計上
        if (label === "取消・返品") flag = true;
      }
      return `<div class="tl-item ${flag ? "flag" : ""}">
        <span class="tl-dot"></span>
        <div><div class="tl-label">${label}${flag ? " ⚠" : ""}</div><div class="tl-date">${esc(d)}</div></div></div>`;
    }).join("") + `</div>`;
  }
  function featJa(f) {
    return ({ amount: "金額", quantity: "数量", unit_price: "単価", margin_rate: "粗利率", payment_terms_days: "支払サイト", credit_utilization: "与信使用率", ship_gap_days: "出荷ギャップ", delivery_gap_days: "検収ギャップ", period_end_proximity_days: "期末近接", discount_rate: "値引率" })[f] || f;
  }
  function evTypeJa(t) {
    return ({ shipment_confirmation: "出荷確認", delivery_proof: "検収・納品証憑", contract_terms: "契約条件", corporate_registry: "法人登記", sanctions_screening: "制裁スクリーニング", related_party_registry: "関連当事者", bank_receipt: "入金記録", price_master: "価格マスタ", communication: "通信", network_path: "取引ネットワーク", other: "その他" })[t] || t;
  }

  // 監査証跡
  function renderAudit() {
    const a = D.audit;
    const view = h("div", { class: "grid" });
    view.appendChild(h("div", { class: `card`, "data-demo": "chain", html: `
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <div style="width:46px;height:46px;border-radius:12px;display:grid;place-items:center;background:${a.chain_valid ? "#ecfdf3" : "#fef3f2"};color:${a.chain_valid ? "#067647" : "#b42318"}">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="${a.chain_valid ? "M20 6 9 17l-5-5" : "M18 6 6 18M6 6l12 12"}"/></svg>
        </div>
        <div style="flex:1;min-width:220px">
          <h2 style="font-size:16px">監査ログ整合性: ${a.chain_valid ? "一致（改ざんなし）" : "不整合を検出"}</h2>
          <p class="small muted" style="margin:4px 0 0">WORM＋ハッシュチェーン。各エントリは直前のハッシュを含み連結します。1件でも改ざん・欠番・並べ替え・末尾切り詰めがあれば検知されます。</p>
        </div>
        <dl class="kv" style="min-width:220px">
          <dt>エントリ数</dt><dd>${num(a.total)}</dd>
          <dt>チェーン長（checkpoint）</dt><dd>${num(a.checkpoint && a.checkpoint.length)}</dd>
          <dt>末尾ハッシュ</dt><dd class="mono">${shortHash(a.checkpoint && a.checkpoint.head_hash)}</dd>
        </dl>
      </div>` }));

    const wrap = h("div", { class: "table-wrap" });
    const table = h("table", { class: "data" });
    table.innerHTML = `<thead><tr><th class="num">seq</th><th>時刻</th><th>主体</th><th>アクション</th><th>対象</th><th>hash</th><th>prev</th></tr></thead>`;
    const tbody = h("tbody");
    const entries = a.entries.slice(-200);
    entries.forEach(e => {
      const role = e.actor.startsWith("human") ? "human" : e.actor;
      const roleClass = role === "agent" ? "role-agent" : role === "system" ? "role-system" : "role-human";
      const tr = h("tr", { style: "cursor:default" });
      tr.innerHTML = `<td class="num mono">${e.seq}</td>
        <td class="small mono">${esc((e.timestamp || "").replace("T", " ").slice(0, 19))}</td>
        <td><span class="chip ${roleClass}">${esc(ROLE_JA[e.actor] || e.actor)}</span></td>
        <td>${esc(actionJa(e.action))}</td>
        <td class="mono small">${esc(e.target || "—")}</td>
        <td class="mono small">${shortHash(e.hash)}</td>
        <td class="mono small">${shortHash(e.prev_hash)}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    view.appendChild(h("div", { class: "small muted", style: "margin:-6px 0 4px" }, `最新 ${entries.length} / 全 ${num(a.total)} エントリを表示`));
    view.appendChild(wrap);
    return view;
  }
  function actionJa(a) {
    return ({ ingest_complete: "取込完了", rules_evaluated: "ルール評価", ml_scored: "MLスコアリング", network_analyzed: "ネットワーク分析", funnel_selected: "ファネル選別", finding_created: "所見生成", observe: "観察", hypothesize: "仮説生成", tool_call: "ツール呼出", evidence_collected: "証憑収集", gate_blocked: "ゲート抑止", verify: "検証", finding_updated: "所見更新", skip_human_decided: "人間判断済みをスキップ" })[a] || a;
  }

  // レポート
  function renderReport() {
    const view = h("div");
    view.appendChild(h("div", { class: "report-md", html: mdToHtml(D.summary_md) }));
    view.appendChild(h("div", { class: "callout info", style: "max-width:860px;margin-top:16px", html: `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex:none"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
      <div>本レポートはプロトタイプのデモ出力です。すべて合成データであり、所見はAIによる提示です。確定・是正・通報・開示は独立した人間が判断します。</div>` }));
    return view;
  }

  // ---- トースト ----
  let toastT;
  function toast(msg) {
    let el = $("#toast");
    if (!el) { el = h("div", { id: "toast", style: "position:fixed;left:50%;bottom:80px;transform:translateX(-50%);background:#0c1523;color:#fff;padding:12px 18px;border-radius:10px;box-shadow:0 12px 32px rgba(16,24,40,.24);z-index:9997;font-size:13.5px;max-width:520px" }); document.body.appendChild(el); }
    el.textContent = msg; el.style.opacity = "1";
    clearTimeout(toastT); toastT = setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .4s"; }, 3200);
  }

  // ---- ルーティング ----
  const ROUTES = [
    { key: "dashboard", label: "ダッシュボード", sub: "母集団・ファネル・データ品質の全体像", icon: iconGrid, render: renderDashboard },
    { key: "findings", label: "所見一覧", sub: "重要度・アサーションで絞り込み", icon: iconList, render: renderFindings, count: () => D.findings.length },
    { key: "console", label: "レビューコンソール", sub: "所見の詳細と人間による確定（HITL）", icon: iconSearch, render: renderConsole, hideNavCount: true },
    { key: "audit", label: "監査証跡", sub: "WORM＋ハッシュチェーンの整合性", icon: iconShield, render: renderAudit, count: () => D.audit.total },
    { key: "report", label: "レポート", sub: "経営者・監査役向けサマリ", icon: iconDoc, render: renderReport },
  ];
  let current = "dashboard", currentParam = null, _hashLock = false;
  function buildNav() {
    const nav = $("#nav"); nav.innerHTML = "";
    ROUTES.forEach(r => {
      const btn = h("button", { class: "nav-item" + (r.key === current ? " active" : ""), "data-route": r.key, onclick: () => navigate(r.key) },
        [h("span", { class: "ico", html: (typeof r.icon === "function" ? r.icon() : r.icon) }), h("span", null, r.label)]);
      if (r.count && !r.hideNavCount) btn.appendChild(h("span", { class: "count" }, num(r.count())));
      nav.appendChild(btn);
    });
  }
  function navigate(key, param) {
    const r = ROUTES.find(x => x.key === key);
    if (!r) key = "dashboard";
    current = key; currentParam = param || null;
    document.querySelectorAll(".nav-item").forEach(b => {
      const on = b.getAttribute("data-route") === key;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    const route = ROUTES.find(x => x.key === key);
    $("#pageTitle").textContent = route.label + (key === "console" && param ? ` — ${param}` : "");
    $("#pageSub").textContent = route.sub;
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(route.render(param));
    window.scrollTo(0, 0);
    window.__RRR_ROUTE = key;
    _hashLock = true;
    location.hash = key + (param ? "/" + encodeURIComponent(param) : "");
    setTimeout(() => { _hashLock = false; }, 0);
  }
  function routeFromHash() {
    const raw = (location.hash || "").replace(/^#/, "");
    if (!raw) return null;
    const [key, param] = raw.split("/");
    return { key, param: param ? decodeURIComponent(param) : null };
  }
  window.addEventListener("hashchange", () => {
    if (_hashLock) return;
    const r = routeFromHash();
    if (r) navigate(r.key, r.param);
  });
  window.RRR = { navigate, get route() { return current; }, data: D };

  // ---- アイコン（インラインSVG）----
  function iconGrid() { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>`; }
  function iconList() { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>`; }
  function iconSearch() { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>`; }
  function iconShield() { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>`; }
  function iconDoc() { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h6"/></svg>`; }
  const iconDb = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>`;
  const iconFilter = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 3H2l8 9.5V19l4 2v-8.5z"/></svg>`;
  const iconAlert = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/></svg>`;
  const iconCheck = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4 12 14.01l-3-3"/></svg>`;

  // ---- 起動 ----
  function boot() {
    if (!window.DEMO_DATA || !D.findings) {
      $("#view").innerHTML = `<div class="callout warn" style="max-width:640px"><div>
        デモデータが読み込まれていません。<br>プロジェクトルートで <code>python scripts/build_demo_data.py</code> を実行して
        <code>web/data/data.js</code> を生成してください。</div></div>`;
      return;
    }
    const eng = D.meta && D.meta.engagement || {};
    const trackJa = eng.track === "track_a" ? "Track A（内部監査/監査役支援）" : "Track B";
    const layerJa = ({ third_line: "3線（検知・独立保証）", second_line: "2線（継続モニタリング）", first_line: "1線（現場）" })[eng.deployment_layer] || eng.deployment_layer;
    $("#engagementBadge").innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> ${trackJa} ・ ${layerJa}`;
    buildNav();
    const r = routeFromHash();
    navigate(r && r.key ? r.key : "dashboard", r && r.param);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
