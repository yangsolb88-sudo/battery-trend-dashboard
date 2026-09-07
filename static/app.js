const state = {
  dashboard: null,
  category: 'briefing',
  scope: 'all',
  period: 'week',
};

const $ = (id) => document.getElementById(id);

function fmtDate(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
}

function changeClass(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '';
  return Number(v) >= 0 ? 'up' : 'down';
}

function pct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '';
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
}

function safeText(v, fallback='-') {
  return (v === null || v === undefined || v === '') ? fallback : v;
}

function renderKpis(kpis=[]) {
  const container = $('kpis');
  const items = kpis.length ? kpis : [
    {label:'한국 Li-ion 배터리 수출', value:'연결 대기', meta:'UN Comtrade'},
    {label:'미국 BESS 운영용량', value:'연결 대기', meta:'U.S. EIA'},
    {label:'국내 기사', value:'조회 대기', meta:'GDELT'},
    {label:'해외 기사', value:'조회 대기', meta:'GDELT'},
  ];
  container.innerHTML = items.slice(0,4).map(item => `
    <article class="metric">
      <span>${safeText(item.label)}</span>
      <div><strong>${safeText(item.value)}</strong>${item.change !== null && item.change !== undefined ? `<b class="${changeClass(item.change)}">${pct(item.change)}</b>` : ''}</div>
      <small>${safeText(item.meta)}</small>
    </article>
  `).join('');
}

function renderKeywords(keywords=[]) {
  $('keywords').innerHTML = keywords.map((k, idx) => `
    <div class="keyword-pill">
      <b>${String(idx+1).padStart(2,'0')}</b>
      <strong>${k.ko || ''}</strong>
      <span>${k.en || ''}</span>
    </div>
  `).join('');
}

function articleLink(a) {
  return `
    <a class="article" href="${a.url}" target="_blank" rel="noopener noreferrer">
      <div class="date">${safeText(a.date)}</div>
      <div class="article-text">
        <strong>${safeText(a.title)}</strong>
        <span>${safeText(a.domain)}${a.language ? ` · ${a.language}` : ''}</span>
      </div>
      <i>↗</i>
    </a>
  `;
}

function renderPreview(target, items=[]) {
  const box = $(target);
  if (!items.length) {
    box.innerHTML = '<div class="empty">최근 7일 기준 표시할 관련 기사가 없음</div>';
    return;
  }
  box.innerHTML = items.slice(0,5).map(articleLink).join('');
}

function renderStatus(connections={}, sources=[]) {
  const entries = Object.values(connections || {});
  const statusHtml = entries.length ? entries.map(c => `
    <div class="status ${c.status || ''}">
      <b>${safeText(c.label)}</b>
      <span>${safeText(c.status)}</span>
      ${c.detail ? `<small>${c.detail}</small>` : ''}
    </div>
  `).join('') : '<div class="empty">연결상태 정보 없음</div>';
  const sourceHtml = sources?.length ? `
    <div class="sources">
      ${sources.map(s => `<a href="${s.url}" target="_blank" rel="noopener noreferrer">${s.title}</a>`).join('')}
    </div>
  ` : '';
  $('statusList').innerHTML = statusHtml + sourceHtml;
}

function categoryMeta(category) {
  const cats = state.dashboard?.categories || {};
  return cats[category] || {title:'주간 브리핑', subtitle:'사용후 배터리 재활용 관련 기사'};
}

async function loadDashboard(force=false) {
  $('domesticPreview').innerHTML = '국내 기사 불러오는 중...';
  $('globalPreview').innerHTML = '해외 기사 불러오는 중...';
  try {
    const res = await fetch(`/api/dashboard${force ? '?refresh=1' : ''}`);
    const data = await res.json();
    state.dashboard = data;
    $('lastUpdated').textContent = fmtDate(data.generated_at);
    renderKpis(data.kpis || []);
    renderKeywords(data.keywords || []);
    renderPreview('domesticPreview', data.weekly?.domestic?.preview || []);
    renderPreview('globalPreview', data.weekly?.global?.preview || []);
    renderStatus(data.connections || {}, data.sources || []);
    await loadArticles();
  } catch (err) {
    renderKpis([]);
    $('domesticPreview').innerHTML = `<div class="empty">대시보드 로딩 실패: ${err.message}</div>`;
    $('globalPreview').innerHTML = `<div class="empty">대시보드 로딩 실패: ${err.message}</div>`;
  }
}

async function loadArticles() {
  const meta = categoryMeta(state.category);
  $('boardTitle').textContent = meta.title;
  $('boardDesc').textContent = `${state.period === 'week' ? '최근 7일' : '최근 6개월'} · ${state.scope === 'all' ? '국내+해외' : state.scope === 'domestic' ? '국내' : '해외'} · ${meta.subtitle}`;
  $('articleList').innerHTML = '<div class="loading">기사 목록을 조회하고 있음...</div>';
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), 20000);
  try {
    const qs = new URLSearchParams({category: state.category, scope: state.scope, period: state.period});
    const res = await fetch(`/api/articles?${qs.toString()}`, {signal: controller.signal});
    clearTimeout(t);
    const data = await res.json();
    const items = data.articles || [];
    $('articleList').innerHTML = items.length
      ? items.map(articleLink).join('') + `<p class="result-note">${data.period_label || ''} · ${items.length}건 표시</p>`
      : '<div class="empty large">해당 조건에서 표시할 관련 기사가 없음</div>';
  } catch (err) {
    clearTimeout(t);
    $('articleList').innerHTML = `<div class="empty large">조회가 오래 걸리거나 실패함. 최근 7일 또는 다른 분야를 선택해 다시 조회 필요<br/><small>${err.name === 'AbortError' ? '20초 초과로 중단됨' : err.message}</small></div>`;
  }
}

document.querySelectorAll('[data-load]').forEach(btn => {
  btn.addEventListener('click', () => {
    state.scope = btn.dataset.load;
    state.category = btn.dataset.category || 'briefing';
    state.period = 'sixmonths';
    document.querySelectorAll('.scope').forEach(x => x.classList.toggle('active', x.dataset.scope === state.scope));
    document.querySelectorAll('.period').forEach(x => x.classList.toggle('active', x.dataset.period === state.period));
    document.querySelectorAll('.topic').forEach(x => x.classList.toggle('active', x.dataset.category === state.category));
    loadArticles();
    document.querySelector('.article-board').scrollIntoView({behavior:'smooth'});
  });
});

document.querySelectorAll('.topic').forEach(btn => {
  btn.addEventListener('click', () => {
    state.category = btn.dataset.category;
    document.querySelectorAll('.topic').forEach(x => x.classList.toggle('active', x === btn));
    loadArticles();
  });
});

document.querySelectorAll('.scope').forEach(btn => {
  btn.addEventListener('click', () => {
    state.scope = btn.dataset.scope;
    document.querySelectorAll('.scope').forEach(x => x.classList.toggle('active', x === btn));
    loadArticles();
  });
});

document.querySelectorAll('.period').forEach(btn => {
  btn.addEventListener('click', () => {
    state.period = btn.dataset.period;
    document.querySelectorAll('.period').forEach(x => x.classList.toggle('active', x === btn));
    loadArticles();
  });
});

$('reloadBtn').addEventListener('click', () => loadDashboard(true));
loadDashboard();
