const sectionOrder = ["MARKET","MATERIALS","POLICY","RECYCLING","COMPANIES","TECHNOLOGY"];
let latestData = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function formatDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return new Intl.DateTimeFormat("ko-KR", { timeZone:"Asia/Seoul", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" }).format(d);
}

function renderCitations(text, sources) {
  const byNumber = new Map((sources || []).map(s => [Number(s.number), s]));
  let safe = escapeHtml(text);
  safe = safe.replace(/\[(\d+)\]/g, (_, n) => {
    const src = byNumber.get(Number(n));
    if (!src) return `[${n}]`;
    const url = escapeHtml(src.url);
    const title = escapeHtml(src.title);
    return `<a href="${url}" target="_blank" rel="noopener noreferrer" title="${title}">[${n}]</a>`;
  });
  return safe;
}

function cardHtml(key, data, index) {
  const sources = data.sources || [];
  const sourceHtml = sources.length ? sources.map(s => `
    <a class="source-link" href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">[${s.number}] ${escapeHtml(s.title)}</a>`).join("") :
    `<span class="source-link">출처 링크 없음</span>`;
  return `
    <article class="brief-card" data-key="${key}">
      <div class="brief-head">
        <div>
          <h3>${escapeHtml(data.title)}</h3>
          <p>${escapeHtml(data.subtitle)}</p>
        </div>
        <span class="brief-index">0${index + 1}</span>
      </div>
      <div class="brief-body">${renderCitations(data.text, sources)}</div>
      <div class="sources">
        <div class="sources-title">SOURCE LINKS</div>
        ${sourceHtml}
      </div>
    </article>`;
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

async function loadDashboard() {
  const btn = document.getElementById("reloadBtn");
  btn.disabled = true;
  btn.textContent = "불러오는 중";
  try {
    const res = await fetch("/api/dashboard", {cache:"no-store"});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    latestData = await res.json();
    const grid = document.getElementById("dashboard");
    grid.innerHTML = sectionOrder.map((key,i) => cardHtml(key, latestData.sections[key], i)).join("");
    document.getElementById("sourceCount").textContent = latestData.all_sources?.length ?? 0;
    document.getElementById("updatedAt").textContent = `${formatDate(latestData.generated_at)} KST`;
    document.getElementById("loading").classList.add("hidden");
    grid.classList.remove("hidden");

    const notice = document.getElementById("notice");
    const label = document.getElementById("connectionLabel");
    if (latestData.status === "live") {
      label.textContent = "LIVE 데이터 연결";
      notice.classList.add("hidden");
    } else {
      label.textContent = latestData.status === "stale" ? "캐시 데이터 표시" : "DEMO 모드";
      notice.classList.remove("hidden");
      notice.textContent = latestData.status === "demo"
        ? "현재 데모 모드임. Render 환경변수에 OPENAI_API_KEY를 추가하면 최신 웹검색 브리핑으로 자동 전환됨."
        : "외부 API 호출에 일시적인 문제가 있어 마지막 정상 데이터를 표시 중임.";
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
document.getElementById("reloadBtn").addEventListener("click", loadDashboard);
loadDashboard();
