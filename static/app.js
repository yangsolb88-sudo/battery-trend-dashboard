const state = { dashboard: null, category: 'briefing', scope: 'all', period: 'sixmonths' };
const $ = (id) => document.getElementById(id);

function fmtDate(v){ if(!v) return '-'; const d = new Date(v); return Number.isNaN(d.getTime()) ? v : d.toLocaleString('ko-KR',{dateStyle:'medium',timeStyle:'short'}); }
function safe(v,f='-'){ return (v===null||v===undefined||v==='') ? f : v; }
function cls(v){ return Number(v) < 0 ? 'down' : 'up'; }
function pct(v){ return (v===null||v===undefined||v==='') ? '' : `${Number(v)>=0?'+':''}${Number(v).toFixed(1)}%`; }

function renderKpis(items=[]){
  $('kpis').innerHTML = items.map(x=>`<article class="kpi"><span>${safe(x.label)}</span><div><strong>${safe(x.value)}</strong>${x.change!==null&&x.change!==undefined?`<b class="${cls(x.change)}">${pct(x.change)}</b>`:''}</div><small>${safe(x.meta,'')}</small></article>`).join('');
}
function renderKeywords(items=[]){
  $('keywordGrid').innerHTML = items.map((k,i)=>`<div class="kw"><b>${String(i+1).padStart(2,'0')}</b><strong>${k.ko}</strong><span>${k.en}</span><small>${k.jp} · ${k.zh}</small></div>`).join('');
}
function article(a){
  return `<a class="article" href="${a.url}" target="_blank" rel="noopener noreferrer"><time>${safe(a.date)}</time><div><strong>${safe(a.title)}</strong><span>${safe(a.domain)} · ${safe(a.source)}</span></div><i>↗</i></a>`;
}
function emptyBox(label, searchUrl, searchUrls=[]){
  const links = (searchUrls && searchUrls.length)
    ? `<div class="fallback-links">${searchUrls.map(x=>`<a href="${x.url}" target="_blank" rel="noopener noreferrer">${x.label || '뉴스 검색'} ↗</a>`).join('')}</div>`
    : (searchUrl ? `<br/><a href="${searchUrl}" target="_blank" rel="noopener noreferrer">뉴스에서 직접 보기 ↗</a>` : '');
  return `<div class="empty"><b>${label}</b><br/>자동 RSS 결과가 적으면 아래 직접검색 링크로 확인하면 됨${links}</div>`;
}
function renderPreview(id, data){
  const box = $(id); const items = data?.articles || [];
  box.innerHTML = items.length ? items.slice(0,5).map(article).join('') : emptyBox('표시할 관련 기사가 없음', data?.search_url, data?.search_urls || []);
}
function renderStatus(connections={}, sources=[]){
  const cards = Object.values(connections).map(c=>`<div class="status-card ${c.status}"><b>${safe(c.label)}</b><span>${safe(c.status)}</span><small>${safe(c.detail,'')}</small></div>`).join('');
  const links = sources.length ? `<div class="source-links">${sources.map(s=>`<a href="${s.url}" target="_blank" rel="noopener noreferrer">${s.title}</a>`).join('')}</div>` : '';
  $('statusList').innerHTML = cards + links;
}
function meta(){ return state.dashboard?.categories?.[state.category] || {title:'종합 브리핑', subtitle:'사용후 배터리 재활용 기사'}; }

async function loadDashboard(force=false){
  $('domesticPreview').innerHTML = '<div class="loading">국내 뉴스 확인 중...</div>';
  $('globalPreview').innerHTML = '<div class="loading">해외 뉴스 확인 중...</div>';
  const res = await fetch(`/api/dashboard${force?'?refresh=1':''}`);
  const data = await res.json();
  state.dashboard = data;
  $('lastUpdated').textContent = fmtDate(data.generated_at);
  renderKpis(data.kpis || []);
  renderKeywords(data.keywords || []);
  renderPreview('domesticPreview', data.weekly?.domestic);
  renderPreview('globalPreview', data.weekly?.global);
  renderStatus(data.connections || {}, data.sources || []);
  await loadArticles();
}

async function loadArticles(){
  const m = meta();
  $('boardTitle').textContent = m.title;
  $('boardDesc').textContent = `${state.period==='week'?'최근 30일':'최근 6개월'} · ${state.scope==='all'?'국내+해외':state.scope==='domestic'?'국내':'해외'} · ${m.subtitle}`;
  $('articleList').innerHTML = '<div class="loading">기사 목록 확인 중...</div>';
  const ctrl = new AbortController();
  const timer = setTimeout(()=>ctrl.abort(), 12000);
  try{
    const qs = new URLSearchParams({category: state.category, scope: state.scope, period: state.period});
    const res = await fetch(`/api/articles?${qs}`, {signal:ctrl.signal});
    clearTimeout(timer);
    const data = await res.json();
    const items = data.articles || [];
    $('articleList').innerHTML = items.length ? items.map(article).join('') + `<p class="result-note">${items.length}건 표시 · ${safe(data.method,'')}</p>` : emptyBox('조건에 맞는 기사 없음', data.search_url, data.search_urls || []);
  }catch(e){
    clearTimeout(timer);
    $('articleList').innerHTML = `<div class="empty">조회가 12초를 초과했음<br/>다시 누르거나 최근 7일 조건으로 조회 필요</div>`;
  }
}

document.querySelectorAll('.topic').forEach(btn=>btn.addEventListener('click',()=>{state.category=btn.dataset.category;document.querySelectorAll('.topic').forEach(x=>x.classList.toggle('active',x===btn));loadArticles();}));
document.querySelectorAll('.scope').forEach(btn=>btn.addEventListener('click',()=>{state.scope=btn.dataset.scope;document.querySelectorAll('.scope').forEach(x=>x.classList.toggle('active',x===btn));loadArticles();}));
document.querySelectorAll('.period').forEach(btn=>btn.addEventListener('click',()=>{state.period=btn.dataset.period;document.querySelectorAll('.period').forEach(x=>x.classList.toggle('active',x===btn));loadArticles();}));
document.querySelectorAll('[data-open]').forEach(btn=>btn.addEventListener('click',()=>{state.scope=btn.dataset.open;state.category='briefing';state.period='sixmonths';document.querySelectorAll('.scope').forEach(x=>x.classList.toggle('active',x.dataset.scope===state.scope));document.querySelectorAll('.period').forEach(x=>x.classList.toggle('active',x.dataset.period===state.period));document.querySelector('.article-board').scrollIntoView({behavior:'smooth'});loadArticles();}));
$('reloadBtn').addEventListener('click',()=>loadDashboard(true));
loadDashboard().catch(e=>{document.body.insertAdjacentHTML('afterbegin',`<div class="fatal">대시보드 로딩 실패: ${e.message}</div>`);});
