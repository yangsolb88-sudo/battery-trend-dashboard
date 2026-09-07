const state = {
  dashboard: null,
  view: "overview",
  category: "briefing",
  scope: "domestic",
  period: "sixmonths",
  year: null,
  month: null,
  articles: [],
  shown: 0,
};

const meta = {
  market: {title:"시장 및 수요", subtitle:"사용후 배터리 발생·회수·재활용 수요·재생원료 시장", scopes:["all"]},
  briefing: {title:"주간 브리핑", subtitle:"국내·해외 핵심 이슈를 최근 7일 기준으로 우선 확인", scopes:["domestic","global"]},
  policy: {title:"정책 동향", subtitle:"법령·EPR·재생원료·배터리여권·인증제도", scopes:["domestic","global"]},
  company: {title:"기업 동향(국내)", subtitle:"국내 사용후 배터리·재활용·재생원료 사업 및 투자", scopes:["domestic"]},
  technology: {title:"기술 동향", subtitle:"회수·전처리·습식/건식·직접재활용·진단·재사용", scopes:["domestic","global"]},
};

function esc(v){return String(v??"").replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function formatDate(iso){if(!iso)return"–";return new Intl.DateTimeFormat("ko-KR",{timeZone:"Asia/Seoul",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}).format(new Date(iso));}
function scopeLabel(scope){return scope==="domestic"?"국내":scope==="global"?"해외":"국내+해외";}

function kpiHtml(k){
  const ch=Number(k.change), has=Number.isFinite(ch);
  return `<article class="kpi-card"><span>${esc(k.label)}</span><div><strong>${esc(k.value)}</strong>${has?`<b class="${ch>=0?'up':'down'}">${ch>0?'+':''}${ch.toFixed(1)}%</b>`:''}</div><small>${esc(k.meta||"")}</small></article>`;
}

function previewHtml(items){
  if(!items?.length)return `<p class="empty">최근 7일 조회 기사 없음</p>`;
  return items.map(a=>`<a class="preview-item" href="${esc(a.url)}" target="_blank" rel="noopener"><b>${esc(a.title)}</b><span>${esc(a.date||"")} · ${esc(a.domain||a.country||"")}</span></a>`).join("");
}

async function loadDashboard(force=false){
  const btn=document.getElementById("reloadBtn"); btn.disabled=true; btn.textContent="불러오는 중";
  try{
    const r=await fetch(`/api/dashboard${force?'?refresh=1':''}`,{cache:"no-store"});
    state.dashboard=await r.json();
    document.getElementById("updatedAt").textContent=`${formatDate(state.dashboard.generated_at)} KST`;
    document.getElementById("kpiGrid").innerHTML=(state.dashboard.kpis||[]).map(kpiHtml).join("");
    document.getElementById("marketIndicators").innerHTML=(state.dashboard.kpis||[]).map(kpiHtml).join("");
    document.getElementById("domesticCount").textContent=state.dashboard.weekly?.domestic?.count??0;
    document.getElementById("globalCount").textContent=state.dashboard.weekly?.global?.count??0;
    document.getElementById("domesticPreview").innerHTML=previewHtml(state.dashboard.weekly?.domestic?.preview);
    document.getElementById("globalPreview").innerHTML=previewHtml(state.dashboard.weekly?.global?.preview);
    const bad=Object.values(state.dashboard.connections||{}).filter(x=>x.status==="error");
    document.getElementById("connectionLabel").textContent=bad.length?"일부 데이터 연결":"무료 API 연결";
    const notice=document.getElementById("notice");
    if(bad.length){notice.classList.remove("hidden");notice.textContent=`일부 보조지표 연결 확인 필요: ${bad.map(x=>x.label).join(', ')}`;} else notice.classList.add("hidden");
    renderArchiveFilters();
  }catch(e){
    const notice=document.getElementById("notice"); notice.classList.remove("hidden"); notice.textContent=`데이터를 불러오지 못함: ${e.message}`;
  }finally{btn.disabled=false;btn.textContent="새로고침";}
}

function showView(view, options={}){
  state.view=view;
  document.querySelectorAll(".nav-item").forEach(b=>b.classList.toggle("active",b.dataset.view===view));
  document.getElementById("overviewView").classList.toggle("hidden",view!=="overview");
  document.getElementById("archiveView").classList.toggle("hidden",view==="overview");
  if(view==="overview"){window.scrollTo({top:0,behavior:"smooth"});return;}
  state.category=view;
  const m=meta[view];
  state.scope=options.scope&&m.scopes.includes(options.scope)?options.scope:m.scopes[0];
  state.period=view==="briefing"?"week":"sixmonths";
  state.year=null; state.month=null;
  document.getElementById("sectionTitle").textContent=m.title;
  document.getElementById("sectionSubtitle").textContent=m.subtitle;
  document.getElementById("marketIndicators").classList.toggle("hidden",view!=="market");
  renderScopeTabs(); renderArchiveFilters(); loadArticles();
  document.getElementById("archiveView").scrollIntoView({behavior:"smooth",block:"start"});
}

function renderScopeTabs(){
  const scopes=meta[state.category].scopes;
  const el=document.getElementById("scopeTabs");
  if(scopes.length===1){el.innerHTML=`<span class="scope-single">${scopeLabel(scopes[0])}</span>`;return;}
  el.innerHTML=scopes.map(s=>`<button class="${state.scope===s?'active':''}" data-scope="${s}">${scopeLabel(s)}</button>`).join("");
}

function archiveMonths(){return state.dashboard?.archive?.months||[];}
function archiveYears(){return state.dashboard?.archive?.years||[];}

function renderArchiveFilters(){
  if(!state.dashboard)return;
  document.querySelectorAll("#quickPeriods button").forEach(b=>b.classList.toggle("active",state.period===b.dataset.period));
  document.getElementById("yearButtons").innerHTML=archiveYears().map(y=>`<button class="${state.period==='year'&&state.year===y?'active':''}" data-year="${y}">${y}년 전체</button>`).join("");
  const selectedYear=state.year||new Date().getFullYear();
  const allowed=new Set(archiveMonths().filter(x=>x.year===selectedYear).map(x=>x.month));
  document.getElementById("monthButtons").innerHTML=Array.from({length:12},(_,i)=>i+1).map(m=>`<button ${allowed.has(m)?'':'disabled'} class="${state.period==='month'&&state.year===selectedYear&&state.month===m?'active':''}" data-month="${m}">${m}월</button>`).join("");
}

function articleHtml(a){
  return `<a class="article-row" href="${esc(a.url)}" target="_blank" rel="noopener">
    <div class="article-date">${esc(a.date||"–")}</div>
    <div class="article-main"><h4>${esc(a.title)}</h4><p>${esc(a.domain||"")}${a.country?` · ${esc(a.country)}`:''}${a.language?` · ${esc(a.language)}`:''}</p></div>
    <div class="article-arrow">↗</div>
  </a>`;
}

function renderArticles(reset=true){
  if(reset)state.shown=0;
  const next=Math.min(state.articles.length,state.shown+40); state.shown=next;
  document.getElementById("articleList").innerHTML=state.articles.slice(0,next).map(articleHtml).join("") || `<div class="empty-large">해당 조건에서 조회된 기사가 없음</div>`;
  document.getElementById("moreBtn").classList.toggle("hidden",next>=state.articles.length);
}

async function loadArticles(force=false){
  const loading=document.getElementById("articleLoading"), list=document.getElementById("articleList");
  loading.classList.remove("hidden"); list.innerHTML=""; document.getElementById("moreBtn").classList.add("hidden");
  const p=new URLSearchParams({category:state.category,scope:state.scope,period:state.period});
  if(state.year)p.set("year",state.year); if(state.month)p.set("month",state.month); if(force)p.set("refresh","1");
  try{
    const r=await fetch(`/api/articles?${p.toString()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json(); state.articles=d.articles||[];
    document.getElementById("resultTitle").textContent=`${d.period_label} · ${scopeLabel(state.scope)} · ${state.articles.length.toLocaleString()}건`;
    document.getElementById("resultMeta").textContent=`${d.start} ~ ${d.end}`;
    document.getElementById("archiveNote").textContent=d.note||"";
    renderArticles(true);
  }catch(e){
    document.getElementById("resultTitle").textContent="기사 조회 실패";
    list.innerHTML=`<div class="empty-large">${esc(e.message)}</div>`;
  }finally{loading.classList.add("hidden");}
}

// Navigation
document.getElementById("nav").addEventListener("click",e=>{const b=e.target.closest(".nav-item");if(b)showView(b.dataset.view);});
document.querySelectorAll("[data-open]").forEach(b=>b.addEventListener("click",()=>showView(b.dataset.open,{scope:b.dataset.scope})));
document.getElementById("scopeTabs").addEventListener("click",e=>{const b=e.target.closest("button[data-scope]");if(!b)return;state.scope=b.dataset.scope;renderScopeTabs();loadArticles();});
document.getElementById("quickPeriods").addEventListener("click",e=>{const b=e.target.closest("button[data-period]");if(!b)return;state.period=b.dataset.period;state.year=null;state.month=null;renderArchiveFilters();loadArticles();});
document.getElementById("yearButtons").addEventListener("click",e=>{const b=e.target.closest("button[data-year]");if(!b)return;state.period="year";state.year=Number(b.dataset.year);state.month=null;renderArchiveFilters();loadArticles();});
document.getElementById("monthButtons").addEventListener("click",e=>{const b=e.target.closest("button[data-month]");if(!b||b.disabled)return;state.period="month";state.year=state.year||new Date().getFullYear();state.month=Number(b.dataset.month);renderArchiveFilters();loadArticles();});
document.getElementById("moreBtn").addEventListener("click",()=>renderArticles(false));
document.getElementById("forceArticleReload").addEventListener("click",()=>loadArticles(true));
document.getElementById("reloadBtn").addEventListener("click",()=>loadDashboard(true));

loadDashboard();
