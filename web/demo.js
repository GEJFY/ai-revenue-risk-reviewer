/* ガイドデモ（ナレーション付き自動再生）
   各画面を自動で操作・遷移しながら、アニメーションカーソル・ハイライト・字幕・音声で解説する。
   音声は data/narration/step-XX.mp3（高品質ニューラルTTS）を優先し、無ければブラウザ内蔵TTS
   （Web Speech API）→ それも無ければ字幕＋所要時間の自動送り、の順にフォールバックする。 */
(function () {
  "use strict";
  const D = window.DEMO_DATA || {};
  const num = (n) => Number(n || 0).toLocaleString("ja-JP");
  const RRR = () => window.RRR;

  const total = (D.population && D.population.transaction_count) || 0;
  const selected = (D.funnel && D.funnel.selected) || 0;
  const crit = (D.breakdown && D.breakdown.by_severity && D.breakdown.by_severity.critical) || 0;
  const nDet = (D.scenarios_detected || []).filter((x) => x.detected).length;
  const nAll = (D.scenarios_detected || []).length;
  const findingForDemo = ((D.findings || []).find((f) => f.finding_id === "F-CUT1") || (D.findings || [])[0] || {}).finding_id;

  function clickConfirm() {
    const b = document.querySelector("#hitlCard .btn.confirm");
    if (b) b.click();
  }

  const STEPS = [
    { id: "01", route: "dashboard", target: "[data-demo=kpis]",
      text: `これは、売上・収益リスクを全件ベースで評価する、監査レビューコンソールのデモです。表示しているデータはすべて合成データで、実在の企業や取引とは関係ありません。` },
    { id: "02", route: "dashboard", target: "[data-demo=funnel]",
      text: `まず、${num(total)}件すべての取引を、ルール・機械学習・ネットワーク分析で低コストに評価します。そのうえで、高リスクな${num(selected)}件だけを、エージェントの深掘り対象に絞り込みます。` },
    { id: "03", route: "dashboard", target: "[data-demo=assertions]",
      text: `すべての所見は、発生・網羅性・正確性・期間帰属といった、財務諸表アサーションに紐づきます。監査人が何を検証すべきかに、直結させる設計です。` },
    { id: "04", route: "dashboard", target: "[data-demo=dq]",
      text: `総勘定元帳との突合、連番の欠番、期間の網羅を確認します。これが、全件を評価したという主張を裏付けます。` },
    { id: "05", route: "findings", target: "[data-demo=filters]",
      text: `こちらが、高リスク所見の一覧です。重要度、アサーション、カテゴリで絞り込めます。今は、重大と高の${num(selected)}件を表示しています。` },
    { id: "06", route: "console", param: findingForDemo, target: "[data-demo=timeline]",
      text: `一件を開きます。受注から出荷、検収、計上までの日付を三角照合します。この取引は、出荷日が収益認識日より後になっており、未出荷での計上が疑われます。` },
    { id: "07", route: "console", param: findingForDemo, target: "[data-demo=evidence]",
      text: `エージェントは、読み取り専用で外部証憑を収集します。契約や通信、出荷、入金を確認し、証憑に埋め込まれた命令には従わず、一次データとの矛盾を検出します。` },
    { id: "08", route: "console", param: findingForDemo, target: "#hitlCard", action: clickConfirm,
      text: `確定と棄却は、人間だけが行えます。AIは、所見の提示と、レビュー中までに限られます。ここでは、人間の判断として確定を記録しました。` },
    { id: "09", route: "audit", target: "[data-demo=chain]",
      text: `エージェントの全行動は、改ざん不能な監査ログに記録されます。ワームとハッシュチェーンにより、一件でも改ざんや欠番があれば検知できます。` },
    { id: "10", route: "report", target: ".report-md",
      text: `最後に、経営者や監査役に向けたサマリを生成します。所見はあくまでAIによる提示であり、確定や是正の判断は、独立した人間が行います。` },
    { id: "11", route: "dashboard", target: "[data-demo=kpis]",
      text: `以上がデモです。全件評価、アサーションへの紐付け、説明可能性、人間による確定、そして改ざん不能なログを、一つのワークフローに統合しています。` },
  ];

  // ---- DOM ----
  const cursor = document.getElementById("cursor");
  const bar = document.getElementById("captionBar");
  const capText = document.getElementById("captionText");
  const capStep = document.getElementById("capStep");
  const capProgress = document.getElementById("capProgress");
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const st = { idx: -1, playing: false, paused: false, audio: null, utter: null, timer: null, resolver: null, lastHi: null };

  function moveCursor(el) {
    if (!cursor || !el) return;
    const r = el.getBoundingClientRect();
    const x = Math.min(window.innerWidth - 40, r.left + Math.min(48, r.width * 0.3));
    const y = r.top + Math.min(38, r.height * 0.3);
    cursor.classList.add("on");
    cursor.style.left = x + "px";
    cursor.style.top = y + "px";
    cursor.classList.remove("click");
    void cursor.offsetWidth;
    setTimeout(() => cursor.classList.add("click"), reduced ? 0 : 700);
  }
  function highlight(el) {
    if (st.lastHi) st.lastHi.classList.remove("demo-highlight");
    st.lastHi = el || null;
    if (el) el.classList.add("demo-highlight");
  }
  function showCaption(text, i) {
    capText.textContent = text;
    capStep.textContent = `${i + 1} / ${STEPS.length}`;
    bar.classList.add("on");
    capProgress.style.width = ((i + 1) / STEPS.length * 100) + "%";
  }

  // 音声（mp3 → Web Speech → タイマ）
  function speak(step) {
    return new Promise((resolve) => {
      st.resolver = resolve;
      let done = false;
      const finish = () => { if (!done) { done = true; st.resolver = null; resolve(); } };

      // 1) mp3
      const audio = new Audio(`data/narration/step-${step.id}.mp3`);
      st.audio = audio;
      let usedAudio = false;
      audio.addEventListener("ended", finish);
      audio.addEventListener("playing", () => { usedAudio = true; });
      audio.addEventListener("error", tryTts);
      audio.play().then(() => { usedAudio = true; }).catch(tryTts);
      // 保険: メタデータが取れない場合の切替
      setTimeout(() => { if (!usedAudio && !done) tryTts(); }, 900);

      function tryTts() {
        if (usedAudio || done) return;
        st.audio = null;
        // 2) Web Speech API
        try {
          const synth = window.speechSynthesis;
          if (synth && typeof SpeechSynthesisUtterance !== "undefined") {
            synth.cancel();
            const u = new SpeechSynthesisUtterance(step.text);
            u.lang = "ja-JP"; u.rate = 1.0; u.pitch = 1.0;
            const jp = (synth.getVoices() || []).find((v) => /ja[-_]JP/i.test(v.lang));
            if (jp) u.voice = jp;
            u.onend = finish; u.onerror = timerFallback;
            st.utter = u;
            synth.speak(u);
            // 一部ブラウザは onend が発火しないため保険タイマ
            setTimeout(() => { if (!done && !synth.speaking) finish(); }, estimate(step.text) + 1500);
            return;
          }
        } catch (e) { /* noop */ }
        timerFallback();
      }
      function timerFallback() {
        if (done) return;
        st.utter = null;
        st.timer = setTimeout(finish, estimate(step.text));
      }
    });
  }
  function estimate(text) { return Math.max(4000, Math.min(13000, (text || "").length * 95)); }

  function stopAudio() {
    if (st.audio) { try { st.audio.pause(); } catch (e) { } st.audio = null; }
    if (window.speechSynthesis) { try { window.speechSynthesis.cancel(); } catch (e) { } }
    if (st.timer) { clearTimeout(st.timer); st.timer = null; }
  }

  async function runStep(i) {
    if (!st.playing) return;
    if (i >= STEPS.length) { finish(); return; }
    st.idx = i;
    const step = STEPS[i];
    RRR().navigate(step.route, step.param);
    await sleep(reduced ? 60 : 420);          // 描画待ち
    const el = document.querySelector(step.target);
    if (el) {
      el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
      await sleep(reduced ? 20 : 320);
      moveCursor(el);
      highlight(el);
    }
    showCaption(step.text, i);
    if (typeof step.action === "function") { await sleep(reduced ? 0 : 900); try { step.action(); } catch (e) { } }
    await speak(step);
    if (st.playing && !st.paused) runStep(i + 1);
  }
  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  function start() {
    if (st.playing) return;
    st.playing = true; st.paused = false;
    document.body.classList.add("demo-running");
    bar.classList.add("on");
    setPauseIcon(false);
    runStep(0);
  }
  function togglePause() {
    if (!st.playing) return;
    st.paused = !st.paused;
    setPauseIcon(st.paused);
    if (st.paused) {
      if (st.audio) { try { st.audio.pause(); } catch (e) { } }
      if (window.speechSynthesis && window.speechSynthesis.speaking) window.speechSynthesis.pause();
      if (st.timer) { clearTimeout(st.timer); st.timer = null; }
    } else {
      if (st.audio) { st.audio.play().catch(() => { }); }
      else if (window.speechSynthesis && window.speechSynthesis.paused) window.speechSynthesis.resume();
      else runStep(st.idx + 1);   // タイマ系はステップ再開
    }
  }
  function finish() { stop(true); }
  function stop(completed) {
    st.playing = false; st.paused = false;
    stopAudio();
    highlight(null);
    if (cursor) cursor.classList.remove("on", "click");
    bar.classList.remove("on");
    capProgress.style.width = completed ? "100%" : "0";
    setTimeout(() => { capProgress.style.width = "0"; }, 600);
    document.body.classList.remove("demo-running");
  }
  function setPauseIcon(paused) {
    const btn = document.getElementById("capPause");
    if (btn) btn.innerHTML = paused
      ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>'
      : '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';
  }

  // ---- 配線 ----
  function wire() {
    const play = document.getElementById("playDemoBtn");
    if (play) play.addEventListener("click", () => { st.playing ? stop(false) : start(); });
    const p = document.getElementById("capPause"); if (p) p.addEventListener("click", togglePause);
    const s = document.getElementById("capStop"); if (s) s.addEventListener("click", () => stop(false));
    document.addEventListener("keydown", (e) => {
      if (!st.playing) return;
      if (e.key === "Escape") stop(false);
      else if (e.key === " ") { e.preventDefault(); togglePause(); }
    });
    // 音声リストの遅延ロード対策
    if (window.speechSynthesis) window.speechSynthesis.getVoices();
    // ?demo=1 で自動再生（デモ共有リンク用）
    if (/[?&]demo=1/.test(location.search)) setTimeout(start, 600);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire); else wire();
  window.RRR_DEMO = { start, stop, togglePause };
})();
