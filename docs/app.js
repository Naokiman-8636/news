(() => {
  "use strict";
  const CFG = window.NEWS_CONFIG || {};
  const $ = (id) => document.getElementById(id);
  const HIGH = 40, MID = 22;           // 重要度のしきい値（スコア）
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 保存できない環境 */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* noop */ } },
  };

  let data = { articles: [], category_labels: {}, sources: [] };
  let activeCats = new Set();
  const selected = new Set();
  const read = new Set(store.get("read", []));

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const w = "日月火水木金土"[d.getDay()];
    return `${d.getMonth() + 1}/${d.getDate()}(${w}) ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };
  const level = (a) => (a.areas.includes("iwate") && a.score >= HIGH ? "high" : a.score >= MID ? "mid" : "");
  const isNew = (a) => a.first_seen === data.updated_at;

  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 2200);
  }

  // ------------------------------------------------------------ 読み込み
  async function load() {
    const res = await fetch(`data/news.json?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    data = await res.json();
    $("updated").textContent = fmtDate(data.updated_at) || "—";
    $("newCount").textContent = data.new_count ?? 0;
    $("total").textContent = data.total ?? data.articles.length;
    buildSourceSelect();
    buildStatus();
    buildHighlights();
    render();
    loadReports();
  }

  // ------------------------------------------------------------ Word 報告書
  async function loadReports() {
    let idx;
    try {
      const r = await fetch(`reports/index.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) return;
      idx = await r.json();
    } catch { return; }
    const reps = idx.reports || [];
    if (!reps.length) return;
    $("reports").hidden = false;
    const latest = {};
    reps.forEach((r) => { if (!latest[r.slug] || r.created > latest[r.slug].created) latest[r.slug] = r; });
    $("reportLinks").innerHTML = ["daily", "weekly", "monthly"].filter((k) => latest[k]).map((k, i) => {
      const r = latest[k];
      return `<a class="btn ${i === 0 ? "primary" : ""}" href="reports/${encodeURIComponent(r.file)}" download="${esc(r.file)}">📄 最新の${esc(r.kind)}（${esc(r.date.slice(5).replace("-", "/"))}・${r.topics}話題）</a>`;
    }).join("");
    $("reportMeta").textContent = "記事を分野別に整理した報告書をダウンロードできます（毎朝自動作成）";
    $("reportList").innerHTML = reps.map((r) =>
      `<li><a href="reports/${encodeURIComponent(r.file)}" download="${esc(r.file)}">${esc(r.date)} ${esc(r.kind)}</a> <span class="small">${r.topics}話題</span></li>`).join("");
  }

  function buildSourceSelect() {
    const sel = $("source");
    const cur = sel.value;
    const counts = {};
    data.articles.forEach((a) => (counts[a.source] = (counts[a.source] || 0) + 1));
    const names = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
    sel.innerHTML = `<option value="">すべての媒体</option>` +
      names.map((n) => `<option value="${esc(n)}">${esc(n)}（${counts[n]}）</option>`).join("");
    sel.value = names.includes(cur) ? cur : "";
  }

  function buildStatus() {
    $("statusBody").innerHTML = (data.sources || []).map((s) => `<tr>
      <td>${esc(s.name)}</td>
      <td class="${s.ok ? "" : "ng"}">${s.ok ? "正常" : "取得失敗"}</td>
      <td>${s.ok ? `${s.kept ?? 0} / ${s.fetched ?? 0}` : esc(s.error || "")}</td></tr>`).join("");
  }

  function buildHighlights() {
    const since = Date.now() - 36 * 3600e3;
    // 同じ出来事の別媒体記事を除いて上位5話題を選ぶ
    const core = (t) => t.normalize("NFKC").replace(/[（(][^（）()]{1,30}[）)]\s*$/, "").replace(/[｜|].*$/, "")
      .replace(/【[^】]*】|\[[^\]]*\]/g, "").replace(/[\s、。・,.!?！？「」『』"':\-]/g, "");
    const bg = (t) => { const s = new Set(); for (let i = 0; i < t.length - 1; i++) s.add(t.slice(i, i + 2)); return s; };
    const similar = (x, y) => { let n = 0; x.forEach((v) => y.has(v) && n++); return n / Math.max(1, Math.min(x.size, y.size)) >= 0.55; };
    const top = [];
    data.articles
      .filter((a) => a.areas.includes("iwate") && new Date(a.published) >= since)
      .sort((a, b) => b.score - a.score)
      .forEach((a) => {
        if (top.length >= 5) return;
        const g = bg(core(a.title));
        if (!top.some((t) => similar(t._bg, g))) top.push({ ...a, _bg: g });
      });
    $("highlights").hidden = top.length === 0;
    $("highlightList").innerHTML = top.map((a) =>
      `<li><a href="${esc(a.url)}" target="_blank" rel="noopener" data-id="${a.id}">${esc(a.title)}</a><span class="src">${esc(a.source)}・${fmtDate(a.published)}</span></li>`).join("");
  }

  // ------------------------------------------------------------ 絞り込み
  function filtered(ignoreCats = false) {
    const q = $("q").value.trim().toLowerCase().split(/[\s　]+/).filter(Boolean);
    const days = Number($("period").value);
    const since = Date.now() - days * 86400e3;
    const src = $("source").value;
    const onlyIwate = $("onlyIwate").checked, onlyMajor = $("onlyMajor").checked;
    const onlyNew = $("onlyNew").checked, hideRead = $("hideRead").checked;
    return data.articles.filter((a) => {
      if (new Date(a.published) < since) return false;
      if (src && a.source !== src) return false;
      if (onlyIwate && !a.areas.includes("iwate")) return false;
      if (onlyMajor && !a.major) return false;
      if (onlyNew && !isNew(a)) return false;
      if (hideRead && read.has(a.id)) return false;
      if (!ignoreCats && activeCats.size && !a.categories.some((c) => activeCats.has(c))) return false;
      if (q.length) {
        const hay = `${a.title} ${a.summary} ${a.source} ${a.keywords.join(" ")}`.toLowerCase();
        if (!q.every((w) => hay.includes(w))) return false;
      }
      return true;
    });
  }

  function buildChips(base) {
    const counts = {};
    base.forEach((a) => a.categories.forEach((c) => (counts[c] = (counts[c] || 0) + 1)));
    const labels = data.category_labels || {};
    const ids = Object.keys(labels).filter((id) => id !== "iwate" && id !== "tohoku");
    $("chips").innerHTML = `<button type="button" class="chip" data-cat="" aria-pressed="${activeCats.size === 0}">すべての分野</button>` +
      ids.map((id) => `<button type="button" class="chip" data-cat="${id}" aria-pressed="${activeCats.has(id)}">${esc(labels[id])}<span class="n">${counts[id] || 0}</span></button>`).join("");
  }

  function render() {
    buildChips(filtered(true));
    const list = filtered();
    if ($("sort").value === "score") list.sort((a, b) => b.score - a.score || b.published.localeCompare(a.published));
    else list.sort((a, b) => b.published.localeCompare(a.published));

    const labels = data.category_labels || {};
    $("list").innerHTML = list.map((a) => {
      const lv = level(a);
      const tags = [
        ...a.areas.map((c) => `<span class="tag area">${esc(labels[c] || c)}</span>`),
        ...a.categories.map((c) => `<span class="tag">${esc(labels[c] || c)}</span>`),
      ].join("");
      return `<li class="card ${lv ? "lv-" + lv : ""} ${read.has(a.id) ? "read" : ""}" data-id="${a.id}">
        <input type="checkbox" aria-label="選択" data-sel="${a.id}" ${selected.has(a.id) ? "checked" : ""}>
        <div>
          <a class="title" href="${esc(a.url)}" target="_blank" rel="noopener" data-id="${a.id}">${esc(a.title)}</a>
          <div class="info">
            ${lv === "high" ? `<span class="badge high">重要</span>` : lv === "mid" ? `<span class="badge mid">注目</span>` : ""}
            ${isNew(a) ? `<span class="badge new">NEW</span>` : ""}
            <span class="source ${a.major ? "major" : ""}">${esc(a.source)}</span>
            <span>${fmtDate(a.published)}</span>
            ${a.keywords.length ? `<span class="kw">🔑 ${esc(a.keywords.slice(0, 6).join("・"))}</span>` : ""}
          </div>
          ${a.summary ? `<p class="summary">${esc(a.summary)}</p>` : ""}
          ${tags ? `<div class="tags">${tags}</div>` : ""}
        </div></li>`;
    }).join("");
    $("count").textContent = `${list.length} 件`;
    $("empty").hidden = list.length > 0;
    $("empty").textContent = data.articles.length === 0
      ? "まだデータがありません。右上の「今すぐ更新」から初回の収集を実行してください。"
      : "条件に合う記事がありません。期間や絞り込みを変えてみてください。";
    updateSel();
    saveFilters();
  }

  function updateSel() {
    $("selCount").textContent = selected.size;
    $("btnCopy").disabled = selected.size === 0;
  }

  // ------------------------------------------------------------ フィルタの記憶
  const FILTER_IDS = ["period", "sort", "onlyIwate", "onlyMajor", "onlyNew", "hideRead"];
  function saveFilters() {
    const f = {};
    FILTER_IDS.forEach((id) => (f[id] = $(id).type === "checkbox" ? $(id).checked : $(id).value));
    f.cats = [...activeCats];
    store.set("filters", f);
  }
  function restoreFilters() {
    const f = store.get("filters", null);
    if (!f) return;
    FILTER_IDS.forEach((id) => {
      if (!(id in f)) return;
      if ($(id).type === "checkbox") $(id).checked = !!f[id]; else $(id).value = f[id];
    });
    activeCats = new Set(f.cats || []);
  }

  // ------------------------------------------------------------ クリップ（コピー）
  function clipText() {
    const arts = data.articles.filter((a) => selected.has(a.id))
      .sort((a, b) => b.published.localeCompare(a.published));
    const today = new Date();
    const head = `■水産関係ニュース（${today.getMonth() + 1}月${today.getDate()}日）${arts.length}件\n`;
    return head + arts.map((a, i) => {
      const d = new Date(a.published);
      return `\n${i + 1}. ${a.title}\n   （${a.source}／${d.getMonth() + 1}月${d.getDate()}日）\n   ${a.url}`;
    }).join("\n") + "\n";
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      const ok = document.execCommand("copy"); ta.remove(); return ok;
    }
  }

  function markRead(id) {
    if (read.has(id)) return;
    read.add(id);
    store.set("read", [...read].slice(-2000));
    document.querySelectorAll(`.card[data-id="${id}"]`).forEach((c) => c.classList.add("read"));
  }

  // ------------------------------------------------------------ 手動更新
  const actionsUrl = `https://github.com/${CFG.repo}/actions/workflows/${CFG.workflow}`;
  function openUpdate() {
    $("actionsLink").href = actionsUrl;
    const tk = store.get("gh_token", "");
    $("token").value = tk;
    $("btnDispatch").hidden = !tk;
    $("dispatchMsg").textContent = "";
    $("updateDialog").showModal();
  }
  async function dispatch() {
    const tk = store.get("gh_token", "");
    if (!tk) return;
    const msg = $("dispatchMsg");
    msg.textContent = "実行を依頼しています…";
    try {
      const res = await fetch(`https://api.github.com/repos/${CFG.repo}/actions/workflows/${CFG.workflow}/dispatches`, {
        method: "POST",
        headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${tk}`, "X-GitHub-Api-Version": "2022-11-28" },
        body: JSON.stringify({ ref: CFG.branch || "main", inputs: { report_days: $("reportDays").value } }),
      });
      if (res.status !== 204) throw new Error(`HTTP ${res.status}`);
      msg.textContent = "更新を開始しました。完了すると自動で再読み込みします（2〜5分）。";
      waitForUpdate(data.updated_at);
    } catch (e) {
      msg.textContent = `実行できませんでした（${e.message}）。トークンの権限・有効期限をご確認ください。`;
    }
  }
  function waitForUpdate(prev) {
    let tries = 0;
    const timer = setInterval(async () => {
      tries++;
      try {
        const r = await fetch(`data/news.json?t=${Date.now()}`, { cache: "no-store" });
        const j = await r.json();
        if (j.updated_at !== prev) {
          clearInterval(timer);
          await load();
          toast(`更新しました：新着 ${j.new_count} 件`);
          $("dispatchMsg").textContent = "更新が完了しました。";
        }
      } catch { /* 次回再試行 */ }
      if (tries > 30) { clearInterval(timer); $("dispatchMsg").textContent = "時間がかかっています。しばらくしてから再読み込みしてください。"; }
    }, 20000);
  }

  // ------------------------------------------------------------ イベント
  function bind() {
    ["q"].forEach((id) => $(id).addEventListener("input", render));
    FILTER_IDS.concat("source").forEach((id) => $(id).addEventListener("change", render));
    $("chips").addEventListener("click", (e) => {
      const b = e.target.closest(".chip");
      if (!b) return;
      const c = b.dataset.cat;
      if (!c) activeCats.clear();
      else activeCats.has(c) ? activeCats.delete(c) : activeCats.add(c);
      render();
    });
    $("list").addEventListener("change", (e) => {
      const id = e.target.dataset.sel;
      if (!id) return;
      e.target.checked ? selected.add(id) : selected.delete(id);
      updateSel();
    });
    document.addEventListener("click", (e) => {
      const a = e.target.closest("a[data-id]");
      if (a) markRead(a.dataset.id);
    });
    document.addEventListener("auxclick", (e) => {
      const a = e.target.closest("a[data-id]");
      if (a) markRead(a.dataset.id);
    });
    $("btnSelectAll").addEventListener("click", () => {
      filtered().forEach((a) => selected.add(a.id));
      render();
    });
    $("btnClear").addEventListener("click", () => { selected.clear(); render(); });
    $("btnCopy").addEventListener("click", async () => {
      toast((await copy(clipText())) ? `${selected.size} 件をコピーしました（メール等に貼り付けできます）` : "コピーできませんでした");
    });
    $("btnUpdate").addEventListener("click", openUpdate);
    $("btnSaveToken").addEventListener("click", () => {
      const v = $("token").value.trim();
      if (!v) return;
      store.set("gh_token", v);
      $("btnDispatch").hidden = false;
      $("dispatchMsg").textContent = "保存しました。「更新を実行」を押してください。";
    });
    $("btnForgetToken").addEventListener("click", () => {
      store.del("gh_token");
      $("token").value = "";
      $("btnDispatch").hidden = true;
      $("dispatchMsg").textContent = "トークンを削除しました。";
    });
    $("btnDispatch").addEventListener("click", dispatch);
  }

  restoreFilters();
  bind();
  load().catch((e) => {
    $("empty").hidden = false;
    $("empty").textContent = `データを読み込めませんでした（${e.message}）。初回は GitHub Actions で一度更新を実行してください。`;
  });
})();
