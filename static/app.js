const sectionOrder = ["DOMESTIC_NEWS","GLOBAL_NEWS","POLICY","COMPANIES","RECYCLING_TECH","MARKET_STATS"];
let latestData = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function formatDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone:"Asia/Seoul", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"
  }).format(d);
}

function renderCitations(text, sources) {
  const byNumber = new Map((sources || []).map(s => [Number(s.number), s]));
  let safe = escapeHtml(text);
  safe = safe.replace(/\[(\d+)\]/g, (_, n) => {
    const src = byNumber.get(Number(n));
    if (!src) return `[${n}]`;
    return `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(src.title)}">[${n}]</a>`;
  });
  return safe;
}

function cardHtml(key, data, index) {
  const sources = data.sources || [];
  const sourceHtml = sources.length ? sources.map(s => `
    <a class="source-link" href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">[${s.number}] ${escapeHtml(s.title)}</a>`).join("") :
    `<span class="source-link muted">출처 링크 없음</span>`;
  const isNews = ["DOMESTIC_NEWS","GLOBAL_NEWS"].includes(key);
  return `
    <article class="brief-card ${isNews ? 'news-card' : ''}" data-key="${key}">
      <div class="brief-head">
        <div>
          <h3>${escapeHtml(data.title)}</h3>
          <p>${escapeHtml(data.subtitle)}</p>
        </div>
        <span class="brief-index">${String(index + 1).padStart(2, "0")}</span>
      </div>
      <div class="brief-body">${renderCitations(data.text, sources)}</div>
      <div class="sources">
        <div class="sources-title">SOURCE LINKS</div>
        ${sourceHtml}
      </div>
    </article>`;
}

function kpiHtml(kpi) {
  const change = Number(kpi.change);
  const hasChange = Number.isFinite(change);
  const changeClass = !hasChange ? "" : change > 0 ? "up" : change < 0 ? "down" : "flat";
  const changeText = hasChange ? `${change > 0 ? "+" : ""}${change.toFixed(1)}%` : "";
  return `
    <article class="kpi-card">
      <div class="kpi-label">${escapeHtml(kpi.label)}</div>
      <div class="kpi-value-row">
        <strong>${escapeHtml(kpi.value)}</strong>
        ${changeText ? `<span class="kpi-change ${changeClass}">${changeText}</span>` : ""}
      </div>
      <div class="kpi-meta">${escapeHtml(kpi.meta || "")}${kpi.source ? ` · ${escapeHtml(kpi.source)}` : ""}</div>
    </article>`;
}

function keywordRows(items) {
  if (!Array.isArray(items) || !items.length) return "";
  return items.map(x => `<tr><td>${escapeHtml(x.no)}</td><td>${escapeHtml(x.ko)}</td><td>${escapeHtml(x.en)}</td><td>${escapeHtml(x.ja)}</td><td>${escapeHtml(x.zh)}</td></tr>`).join("");
}

function setFocus(key) {
  const grid = document.getElementById("dashboard");
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.section === key));
  if (key === "overview") {
    grid.classList.remove("focus-mode");
    document.querySelectorAll(".brief-card").forEach(c => c.classList.remove("focus"));
    window.scrollTo({top:0, behavior:"smooth"});
    return;
  }
  grid.classList.add("focus-mode");
  document.querySelectorAll(".brief-card").forEach(c => c.classList.toggle("focus", c.dataset.key === key));
  grid.scrollIntoView({behavior:"smooth", block:"start"});
}

function connectionSummary(data) {
  if (!data) return "";
  const conns = data.connections || {};
  if (conns.gdelt?.status === "error") return "뉴스 API 연결 확인 필요: GDELT";
  const optional = Object.entries(conns)
    .filter(([k, v]) => v.status === "limited" && !["gdelt", "alpha"].includes(k))
    .map(([k, v]) => v.label);
  if (optional.length) return `보조 통계 일부 제한: ${optional.join(", ")}`;
  return "";
}

async function loadDashboard(force = false) {
  const btn = document.getElementById("reloadBtn");
  btn.disabled = true;
  btn.textContent = "불러오는 중";
  try {
    const res = await fetch(`/api/dashboard${force ? "?force=true" : ""}`, {cache:"no-store"});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    latestData = await res.json();

    const grid = document.getElementById("dashboard");
    grid.innerHTML = sectionOrder.map((key,i) => cardHtml(key, latestData.sections[key], i)).join("");
    document.getElementById("kpiGrid").innerHTML = (latestData.kpis || []).map(kpiHtml).join("");
    document.getElementById("sourceCount").textContent = latestData.all_sources?.length ?? 0;
    document.getElementById("updatedAt").textContent = `${formatDate(latestData.generated_at)} KST`;
    document.getElementById("domesticCount").textContent = latestData.news_counts?.domestic ?? 0;
    document.getElementById("globalCount").textContent = latestData.news_counts?.global ?? 0;
    const keyRows = keywordRows(latestData.keyword_catalog);
    if (keyRows) document.getElementById("keywordBody").innerHTML = keyRows;
    document.getElementById("loading").classList.add("hidden");
    grid.classList.remove("hidden");

    const notice = document.getElementById("notice");
    const label = document.getElementById("connectionLabel");
    const warning = connectionSummary(latestData);
    if (latestData.status === "live") {
      label.textContent = "뉴스·통계 연결";
    } else {
      label.textContent = "일부 연결 확인";
    }
    if (warning) {
      notice.classList.remove("hidden");
      notice.textContent = warning;
    } else {
      notice.classList.add("hidden");
    }
  } catch (e) {
    document.getElementById("notice").classList.remove("hidden");
    document.getElementById("notice").textContent = `데이터를 불러오지 못함: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "새로고침";
  }
}

document.getElementById("nav").addEventListener("click", e => {
  const btn = e.target.closest(".nav-item");
  if (btn) setFocus(btn.dataset.section);
});
document.getElementById("reloadBtn").addEventListener("click", () => loadDashboard(true));
loadDashboard(false);
