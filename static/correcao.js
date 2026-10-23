// ---------- aba "Corrigir e baixar": correção em 4 etapas ----------
// Cada mudança só entra no arquivo se o usuário marcar. O plano aprovado vai para o servidor,
// que sempre aplica tudo a partir do arquivo original (voltar e mudar de ideia é seguro).
const FIXCOR = { antes: '#ff3b30', depois: '#34c759', a: '#3d9bff', b: '#ffb020', inter: '#ff2d55', apaga: '#ff3b30', preenche: '#34c759', talhao: '#ffffff' };
const ETAPAS = [
  { n: 1, t: 'Simplificar' },
  { n: 2, t: 'Corrigir erros' },
  { n: 3, t: 'Revisar e baixar' },
];
let fix = null, fixMap = null, fixLayer = null;
const fmtInt = n => Math.round(n).toLocaleString('pt-BR');
const fmtPct = (v, d = 2) => (v > 0 ? '+' : '') + fmt(v, d) + '%';

function fixNovo() {
  return {
    step: 1, tol: 1, escopo: 'muitos', simp: null, aprovSimp: new Set(),
    buracos: new Map(), partes: new Map(), invertidos: new Set(), remover: new Set(),
    recalc: false, resumo: null, baixado: false,
    sel: null, busy: '', erro: '',
  };
}

function fixReset() {
  fix = fixNovo();
  etapaEnquadrada = 0;
  indexarGeo();
  if (fixLayer) fixLayer.clearLayers();
  $('fixCap').textContent = 'Clique num item da lista para ver ele aqui.';
  if (!$('viewFix').hidden) fixRender();
}

function fixShow() {
  fixRender();
  if (!fixDisponivel()) return;
  if (!window.L) { $('fixmap').outerHTML = '<div class="mapmsg" id="fixmap">O mapa de prévia precisa de internet.</div>'; return; }
  if (!fixMap) {
    // começa com uma vista qualquer: sem ela o Leaflet não remede o mapa quando ele muda de tamanho
    fixMap = L.map('fixmap', { preferCanvas: true, maxZoom: 19 }).setView([-14, -52], 5);
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Imagens © Esri' }).addTo(fixMap);
    L.control.scale({ imperial: false }).addTo(fixMap);
    const leg = L.control({ position: 'bottomleft' });
    leg.onAdd = () => {
      const d = L.DomUtil.create('div', 'legenda');
      d.id = 'fixLegenda';
      d.title = 'Clique para esconder ou mostrar a legenda';
      L.DomEvent.disableClickPropagation(d);
      d.onclick = () => d.classList.toggle('mini');  // escondida, mostra só o título
      return d;
    };
    leg.addTo(fixMap);
    fixLayer = L.layerGroup().addTo(fixMap);
  }
  setTimeout(() => { fixMap.invalidateSize(); desenharMapa(); }, 50);
}

// ---------- mapa ao lado: visão geral da etapa ou um item em detalhe ----------
let etapaEnquadrada = 0;
const geoIdx = new Map();  // idx -> contornos e buracos do talhão (em graus), para desenhar sem pedir ao servidor
function indexarGeo() {
  geoIdx.clear();
  for (const o of state.res.outros || []) geoIdx.set(o.idx, { outers: o.r, holes: [], tal: o.tal });
  for (const it of state.res.items || []) geoIdx.set(it.idx, { outers: it.outers, holes: it.holes.map(h => h.r), tal: it.tal });
}
const centroAnel = r => { const n = Math.max(1, r.length - 1); let x = 0, y = 0; for (let k = 0; k < n; k++) { x += r[k][0]; y += r[k][1]; } return [y / n, x / n]; };

function legenda(html) { const d = $('fixLegenda'); if (d) d.innerHTML = html; }
const chave = (cor, txt, tipo = '', fundo = .35) => `<div><i class="${tipo}" style="border-color:${cor};background:${tipo === 'x' ? 'transparent' : cor + Math.round(fundo * 255).toString(16).padStart(2, '0')}"></i>${txt}</div>`;

function desenharMapa() {
  if (!fixLayer || !fixDisponivel()) return;
  if (fix.sel) return;  // um item em detalhe fica na tela até o usuário voltar
  $('fixGeral').hidden = true;
  $('fixMapTitulo').textContent = ['', 'Todos os talhões da etapa 1', 'Todos os erros', 'Como o arquivo fica'][fix.step];
  fixLayer.clearLayers();
  const grupo = L.featureGroup().addTo(fixLayer);
  const fundo = () => { for (const g of geoIdx.values()) g.outers.forEach(r => L.polygon(ll(r), { color: '#ffffff', weight: .7, opacity: .4, fill: false, interactive: false }).addTo(fixLayer)); };
  const clicavel = (camada, sel, dica) => camada.bindTooltip(dica, { sticky: true }).on('click', () => selecionar(sel)).addTo(grupo);
  // de longe o talhão some; um ponto no meio dele ajuda a achar e clicar
  const pontoLonge = (g, cor, sel, dica) => {
    if (g && g.outers[0]) clicavel(L.circleMarker(centroAnel(g.outers[0]), { radius: 4, color: cor, weight: 1.5, fillColor: cor, fillOpacity: .9 }), sel, dica);
  };
  let cap = '';
  if (fix.step === 1) {
    fundo();
    legenda('<b>Simplificação</b>' + chave(FIXCOR.depois, 'Aprovado') + chave('#ffffff', 'Recomendado, ainda não aprovado', '', .1) + chave(FIXCOR.b, 'Não recomendado') + chave('#ffffff', 'Outros talhões', 'x'));
    if (fix.simp) for (const t of fix.simp.talhoes) {
      const g = geoIdx.get(t.idx); if (!g) continue;
      const ap = fix.aprovSimp.has(t.idx), cor = !t.recomendado ? FIXCOR.b : ap ? FIXCOR.depois : '#ffffff';
      const dica = `Talhão ${esc(t.tal)} · ${fmtInt(t.nv0)} → ${fmtInt(t.nv1)} vértices${ap ? ' · aprovado' : ''}`;
      g.outers.forEach(r => clicavel(L.polygon(ll(r), { color: cor, weight: 2, fillColor: cor, fillOpacity: ap ? .35 : .1 }), 's' + t.idx, dica));
      pontoLonge(g, cor, 's' + t.idx, dica);
    }
    cap = fix.simp ? `${fmtInt(fix.simp.talhoes.length)} talhões nesta etapa, ${fmtInt(fix.aprovSimp.size)} aprovados. Clique num talhão (no mapa ou na lista) para ver os vértices de antes e de depois.`
      : 'Calcule a prévia para ver no mapa quais talhões seriam simplificados.';
  } else if (fix.step === 2) {
    fundo();
    legenda('<b>Erros</b>' + chave(FIXCOR.preenche, 'Buraco que vai ser preenchido', '', .6) + chave(FIXCOR.antes, 'Buraco que fica', '', .2)
      + chave(FIXCOR.apaga, 'Parte que vai ser apagada', '', .6) + chave(FIXCOR.b, 'Parte solta que fica', '', .2) + chave('#c58bff', 'Desenho com problema', 'p', .8));
    const semArea = new Set(state.res.correcao.semArea || []);
    for (const it of state.res.items) {
      const hs = errHoles(it), ps = errExtra(it);
      if (!hs.length && !ps.length) continue;
      it.outers.forEach(r => clicavel(L.polygon(ll(r), { color: '#ffffff', weight: 1.5, fillOpacity: .04 }), 'e' + it.idx, `Talhão ${esc(it.tal)}`));
      const algum = hs.some(x => (fix.buracos.get(it.idx) || new Set()).has(x.ri)) || ps.some(x => (fix.partes.get(it.idx) || new Set()).has(x.ri));
      pontoLonge(geoIdx.get(it.idx), algum ? FIXCOR.depois : FIXCOR.antes, 'e' + it.idx,
        `Talhão ${esc(it.tal)} · ${hs.length} ${hs.length === 1 ? 'buraco' : 'buracos'}, ${ps.length} ${ps.length === 1 ? 'parte solta' : 'partes soltas'}${algum ? ' · tem correção aprovada' : ''}`);
      hs.forEach((x, k) => {
        const sim = (fix.buracos.get(it.idx) || new Set()).has(x.ri);
        clicavel(L.polygon(ll(x.r), { color: sim ? FIXCOR.preenche : FIXCOR.antes, weight: 1.5, fillColor: sim ? FIXCOR.preenche : FIXCOR.antes, fillOpacity: sim ? .6 : .2 }),
          'e' + it.idx, `Talhão ${esc(it.tal)} · buraco ${k + 1} (${fmt(x.ha)} ha): ${sim ? 'vai ser preenchido' : 'fica como está'}`);
      });
      ps.forEach((x, k) => {
        const sim = (fix.partes.get(it.idx) || new Set()).has(x.ri);
        clicavel(L.polygon(ll(x.r), { color: sim ? FIXCOR.apaga : FIXCOR.b, weight: 2, dashArray: sim ? '6 4' : null, fillColor: sim ? FIXCOR.apaga : FIXCOR.b, fillOpacity: sim ? .6 : .2 }),
          'e' + it.idx, `Talhão ${esc(it.tal)} · parte ${String.fromCharCode(65 + (k % 26))} (${fmt(x.ha)} ha): ${sim ? 'vai ser apagada' : 'fica como está'}`);
      });
    }
    for (const idx of new Set([...state.res.items.filter(i => i.invalid).map(i => i.idx), ...semArea])) {
      const g = geoIdx.get(idx), p = g && g.outers[0] && g.outers[0][0];
      if (p) clicavel(L.circleMarker([p[1], p[0]], { radius: 7, color: '#c58bff', weight: 2, fillOpacity: .8 }), 'e' + idx, `Talhão ${esc(g.tal)} · desenho com problema`);
    }
    const n = nEtapa2();
    cap = `Cada cor mostra o que acontece com cada buraco e parte solta. ${n ? `${fmtInt(n)} ${n === 1 ? 'mudança aprovada' : 'mudanças aprovadas'} até agora.` : 'Nada aprovado ainda.'} Clique num talhão para ver de perto.`;
  } else if (fix.step === 3) {
    legenda('<b>Resultado</b>' + chave(FIXCOR.depois, 'Como o talhão fica') + chave(FIXCOR.antes, 'Como era (tracejado)', 'x') + chave('#ffffff', 'Talhões que não mudam', 'x'));
    const mudou = new Set(fix.resumo ? fix.resumo.lista.map(x => x.idx) : []);
    for (const [idx, g] of geoIdx) if (!mudou.has(idx)) g.outers.forEach(r => L.polygon(ll(r), { color: '#ffffff', weight: .7, opacity: .4, fill: false, interactive: false }).addTo(fixLayer));
    if (fix.resumo) {
      for (const x of fix.resumo.lista) {
        const g = geoIdx.get(x.idx);
        if (g) [...g.outers, ...g.holes].forEach(r => L.polyline(ll(r), { color: FIXCOR.antes, weight: 1.2, dashArray: '4 4', interactive: false }).addTo(fixLayer));
        x.geo.forEach(pg => clicavel(L.polygon(pg.map(ll), { color: FIXCOR.depois, weight: 2, fillColor: FIXCOR.depois, fillOpacity: .25 }), 'r' + x.idx,
          `Talhão ${esc(x.tal)} · ${esc(x.mudancas.join(' · '))}`));
      }
    }
    cap = fix.resumo ? `Verde é como cada talhão alterado fica; o tracejado vermelho é como era. Clique num talhão (no mapa ou na tabela) para comparar de perto.` : '';
  }
  $('fixCap').textContent = cap;
  if (etapaEnquadrada !== fix.step && grupo.getLayers().length) { enquadrar(grupo.getBounds(), 20); etapaEnquadrada = fix.step; }
}

// clique no mapa ou na lista: marca o item dos dois lados e mostra em detalhe
function selecionar(sel) {
  const linha = document.querySelector(`#fixMain [data-sel="${sel}"]`);
  if (linha) {
    document.querySelectorAll('#fixMain [data-sel]').forEach(r => r.classList.toggle('sel', r === linha));
    linha.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  const k = sel.slice(1);
  ({ s: () => verSimp(+k), e: () => verErro(+k), r: () => verRes(+k) })[sel[0]]();
}

// os contornos dos talhões sem problema chegaram do servidor: entram nos desenhos do mapa
function fixContornos() {
  indexarGeo();
  if (fixLayer && !fix.sel && !$('viewFix').hidden) desenharMapa();
}

function detalhe(sel, titulo) {
  fix.sel = sel;
  document.querySelectorAll('#fixMain [data-sel]').forEach(r => r.classList.toggle('sel', r.dataset.sel === sel));
  $('fixGeral').hidden = false;
  $('fixMapTitulo').textContent = titulo;
  legenda('');
}

function voltarGeral() {
  fix.sel = null;
  document.querySelectorAll('#fixMain [data-sel]').forEach(r => r.classList.remove('sel'));
  etapaEnquadrada = 0;
  desenharMapa();
}

function verRes(idx) {
  const x = fix.resumo && fix.resumo.lista.find(i => i.idx === idx);
  if (!x || !fixLayer) return;
  detalhe('r' + idx, `Talhão ${x.tal}: antes e depois`);
  fixLayer.clearLayers();
  const g = geoIdx.get(idx), lim = L.featureGroup().addTo(fixLayer);
  if (g) [...g.outers, ...g.holes].forEach(r => L.polyline(ll(r), { color: FIXCOR.antes, weight: 2, dashArray: '5 4' }).addTo(lim));
  x.geo.forEach(pg => L.polygon(pg.map(ll), { color: FIXCOR.depois, weight: 3, fillColor: FIXCOR.depois, fillOpacity: .2 }).addTo(lim));
  if (lim.getLayers().length) enquadrar(lim.getBounds(), 30);
  legenda('<b>Talhão ' + esc(x.tal) + '</b>' + chave(FIXCOR.antes, 'Antes', 'x') + chave(FIXCOR.depois, 'Depois'));
  $('fixCap').innerHTML = `<b>Talhão ${esc(x.tal)}</b> · ${esc(rotulo(x))}<br>${x.mudancas.map(esc).join('<br>')}<br>Área: ${fmt(x.ha0)} → ${fmt(x.ha1)} ha`;
}

// o tamanho do mapa muda quando a lista ao lado é redesenhada; recalcula antes de enquadrar
// se o mapa ainda não tem tamanho (aba escondida), guarda o enquadramento e faz quando aparecer
let enquadrarDepois = null;
function enquadrar(limites, margem) {
  fixMap.invalidateSize();
  if (!fixMap.getSize().x) { enquadrarDepois = [limites, margem]; return; }
  enquadrarDepois = null;
  fixMap.fitBounds(limites, { padding: [margem, margem], maxZoom: 18 });
}
if (window.ResizeObserver) new ResizeObserver(() => {
  if (!fixMap) return;
  fixMap.invalidateSize();
  if (enquadrarDepois && fixMap.getSize().x) enquadrar(...enquadrarDepois);
}).observe($('fixmap'));

const fixDisponivel = () => !state.example && state.res.correcao && state.res.correcao.sessao && state.res.correcao.podeCorrigir;

// ---------- plano ----------
function fixPlano(ate = 4) {
  const p = { tolerancia: fix.tol, simplificar: [...fix.aprovSimp], buracos: {}, partes: {}, invertidos: [], remover: [], recalcularArea: fix.recalc };
  if (ate >= 2) {
    for (const [k, v] of fix.buracos) if (v.size) p.buracos[k] = [...v];
    for (const [k, v] of fix.partes) if (v.size) p.partes[k] = [...v];
    p.invertidos = [...fix.invertidos];
    p.remover = [...fix.remover];
  }
  return p;
}
const nEtapa2 = () => [...fix.buracos.values(), ...fix.partes.values()].reduce((s, v) => s + v.size, 0) + fix.invertidos.size + fix.remover.size;
// mudou alguma aprovação: o resumo da revisão precisa ser montado de novo
function invalidar() {
  fix.resumo = null;
  fix.baixado = false;
}

async function fixApi(caminho, corpo) {
  const r = await fetch(`api/correcao/${state.res.correcao.sessao}/${caminho}`, corpo
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) } : {});
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.erro || `O servidor respondeu com erro ${r.status}.`);
  }
  return r;
}

async function carregar(msg, fn) {
  fix.busy = msg; fix.erro = '';
  fixRender();
  try { await fn(); } catch (e) { fix.erro = e.message; }
  fix.busy = '';
  fixRender();
}

// ---------- desenho ----------
function fixRender() {
  if (!fix) fix = fixNovo();
  $('fixCount').textContent = fixDisponivel() ? `etapa ${fix.step} de 3` : '3 etapas';
  if (!fixDisponivel()) {
    $('fixIntro').innerHTML = `<div class="loading">${state.example
      ? 'Envie o .zip do seu shapefile no quadro lá em cima. Depois da verificação, esta aba guia você para simplificar o arquivo e corrigir os erros, aprovando cada mudança.'
      : esc((state.res.correcao && state.res.correcao.motivo) || 'A correção não está disponível para este arquivo.')}</div>`;
    $('fixSteps').hidden = $('fixGrid').hidden = true;
    return;
  }
  $('fixIntro').innerHTML = '';
  $('fixSteps').hidden = $('fixGrid').hidden = false;
  const sub = {
    1: fix.simp ? `${fmtInt(fix.aprovSimp.size)} aprovados` : 'Tirar vértices demais',
    2: `${fmtInt(nEtapa2())} ${nEtapa2() === 1 ? 'mudança aprovada' : 'mudanças aprovadas'}`,
    3: fix.baixado ? 'Arquivo baixado' : 'Conferir e gerar o .zip',
  };
  $('fixSteps').innerHTML = ETAPAS.map(e => `<button type="button" class="step ${e.n < fix.step ? 'done' : ''}" data-step="${e.n}" ${e.n === fix.step ? 'aria-current="step"' : ''}>
    <span class="n">${e.n < fix.step ? '✓' : e.n}</span><span><b>${e.t}</b><small>${sub[e.n]}</small></span></button>`).join('');
  $('fixMain').innerHTML = [null, etapa1, etapa2, etapaRevisao][fix.step]()
    + (fix.erro ? `<div class="status err">${esc(fix.erro)}</div>` : '') + navbar();
  if (fix.sel) document.querySelectorAll('#fixMain [data-sel]').forEach(r => r.classList.toggle('sel', r.dataset.sel === fix.sel));
  desenharMapa();
}

function navbar() {
  const prox = { 1: 'Próxima: corrigir erros', 2: 'Próxima: revisar e baixar' }[fix.step];
  const info = {
    1: fix.simp ? `Você aprovou ${fmtInt(fix.aprovSimp.size)} de ${fmtInt(fix.simp.talhoes.length)} talhões. Os outros ficam como estão.` : 'Se não quiser simplificar, é só seguir para a próxima etapa.',
    2: `Você aprovou ${fmtInt(nEtapa2())} ${nEtapa2() === 1 ? 'mudança' : 'mudanças'}. O que não estiver marcado fica como está.`,
    3: '',
  }[fix.step];
  return `<div class="navbar">
    ${fix.step > 1 ? `<button type="button" class="btn ghost" data-act="voltar">← Voltar</button>` : '<span></span>'}
    <span class="note">${info}</span>
    ${prox ? `<button type="button" class="btn" data-act="avancar">${prox} →</button>` : ''}</div>`;
}

const carregando = msg => `<div class="loading"><span class="busy"></span>${esc(msg)}</div>`;
const rotulo = r => `${r.fazNome || 'Fazenda ' + r.faz}${r.fazNome ? ` (cód. ${r.faz})` : ''}`;
function porFazenda(lista, linha) {
  const grupos = new Map();
  for (const x of lista) { const k = rotulo(x); if (!grupos.has(k)) grupos.set(k, []); grupos.get(k).push(x); }
  return [...grupos.entries()].sort((a, b) => a[0].localeCompare(b[0], 'pt-BR', { numeric: true }))
    .map(([k, xs]) => `<div class="farmhead">${esc(k)} · ${xs.length}</div>` + xs.sort((a, b) => String(a.tal).localeCompare(String(b.tal), 'pt-BR', { numeric: true })).map(linha).join('')).join('');
}

// ---------- etapa 1 ----------
function etapa1() {
  const c = state.res.correcao;
  let h = `<div class="explain"><h3>1. Simplificar os contornos</h3>
    <p><b>${fmtInt(c.muitos)} talhões têm muitos vértices</b> (mais de ${fmtInt(c.limite)}, ou um ponto a cada menos de ${c.espacamento} m). O arquivo inteiro tem ${fmtInt(c.total)} vértices.</p>
    <p>Simplificar tira os pontos que quase não mudam o desenho. Com tolerância de 1 m, nenhum trecho do contorno sai mais de 1 m do lugar. O arquivo fica mais leve e o desenho continua praticamente igual.</p>
    <p class="note">Nada muda no arquivo até você marcar. Clique num talhão para ver no mapa o antes (vermelho) e o depois (verde).</p></div>
    <div class="controls">
      <label class="field">Tolerância<select id="fxTol">${[0.5, 1, 2, 5].map(v => `<option value="${v}" ${v === fix.tol ? 'selected' : ''}>${fmt(v, v % 1 ? 1 : 0)} m${v === 1 ? ' (recomendado)' : ''}</option>`).join('')}</select></label>
      <div class="field">Quais talhões<div class="radios">
        <label><input type="radio" name="fxEsc" value="muitos" ${fix.escopo === 'muitos' ? 'checked' : ''}> Só os com muitos vértices (${fmtInt(c.muitos)})</label>
        <label><input type="radio" name="fxEsc" value="todos" ${fix.escopo === 'todos' ? 'checked' : ''}> Todos os talhões (${fmtInt(state.res.total)})</label></div></div>
      <button type="button" class="btn" data-act="calcSimp">${fix.simp ? 'Calcular de novo' : 'Calcular prévia'}</button>
    </div>`;
  if (fix.busy) return h + carregando(fix.busy);
  if (!fix.simp) return h;
  const ts = fix.simp.talhoes, rec = ts.filter(t => t.recomendado);
  const nv0 = ts.reduce((s, t) => s + t.nv0, 0), nv1 = ts.reduce((s, t) => s + t.nv1, 0);
  const maior = ts.reduce((m, t) => Math.max(m, Math.abs(t.var)), 0);
  h += `<div class="sumline"><span>${fmtInt(ts.length)} talhões · vértices <b>${fmtInt(nv0)} → ${fmtInt(nv1)}</b> (${fmtPct((nv1 - nv0) / nv0 * 100, 0)}) · maior mudança de área ${fmt(maior, 2)}%
      ${rec.length < ts.length ? `<br><span class="note">${ts.length - rec.length} não são recomendados porque a área mudaria mais de ${fmt(fix.simp.variacaoMax, 1)}%.</span>` : ''}</span>
    <span class="btns"><button type="button" class="btn ghost small" data-act="simpTodos">Aprovar os ${fmtInt(rec.length)} recomendados</button>
    <button type="button" class="btn ghost small" data-act="simpNenhum">Desmarcar todos</button></span></div>
    <div class="fixlist">${porFazenda(ts, t => `<div class="fixrow ${fix.sel === 's' + t.idx ? 'sel' : ''}" data-sel="s${t.idx}">
      <input type="checkbox" data-simp="${t.idx}" ${fix.aprovSimp.has(t.idx) ? 'checked' : ''} aria-label="Aprovar simplificação do talhão ${esc(t.tal)}">
      <div><span class="t">Talhão ${esc(t.tal)}</span>${t.safra ? ` <span class="s">safra ${esc(t.safra)}</span>` : ''}
        ${t.recomendado ? '' : `<div class="badges"><span class="badge warn">Não recomendado: a área muda ${fmtPct(t.var)}</span></div>`}</div>
      <div class="num">${fmtInt(t.nv0)} → ${fmtInt(t.nv1)} vértices<br><span class="s">área ${fmtPct(t.var, 3)}</span></div></div>`)}</div>`;
  return h;
}

async function verSimp(idx) {
  const t = fix.simp.talhoes.find(x => x.idx === idx);
  detalhe('s' + idx, `Talhão ${t.tal}: vértices antes e depois`);
  $('fixCap').textContent = 'Carregando o talhão…';
  try {
    const d = await (await fixApi(`talhao/${idx}?tol=${fix.tol}`)).json();
    if (fix.sel !== 's' + idx) return;
    fixLayer.clearLayers();
    const antes = L.polygon(d.antes.map(p => p.map(ll)), { color: FIXCOR.antes, weight: 2, dashArray: '5 4', fill: false }).addTo(fixLayer);
    L.polygon(d.depois.map(p => p.map(ll)), { color: FIXCOR.depois, weight: 3, fillColor: FIXCOR.depois, fillOpacity: .12 }).addTo(fixLayer);
    for (const [cor, pol, r] of [[FIXCOR.antes, d.antes, 2], [FIXCOR.depois, d.depois, 3]])
      for (const p of pol) for (const anel of p) for (const [x, y] of anel) L.circleMarker([y, x], { radius: r, color: cor, weight: 1, fillOpacity: 1, interactive: false }).addTo(fixLayer);
    enquadrar(antes.getBounds(), 20);
    legenda(`<b>Talhão ${esc(t.tal)}</b>` + chave(FIXCOR.antes, `Antes: ${fmtInt(d.nv0)} vértices`, 'x') + chave(FIXCOR.depois, `Depois: ${fmtInt(d.nv1)} vértices`));
    $('fixCap').innerHTML = `<b>Talhão ${esc(t.tal)}</b> · ${esc(rotulo(t))}<br>Antes: ${fmtInt(d.nv0)} vértices (vermelho, tracejado) · Depois: ${fmtInt(d.nv1)} (verde). Dê zoom para ver a diferença.`;
  } catch (e) { $('fixCap').textContent = e.message; }
}

// ---------- etapa 2 ----------
function etapa2() {
  const items = state.res.items, c = state.res.correcao;
  const comBuraco = items.filter(i => errHoles(i).length), comParte = items.filter(i => errExtra(i).length);
  const semArea = new Set(c.semArea || []);
  const invalidos = [...items.filter(i => i.invalid).map(i => i.idx), ...semArea].filter((v, k, a) => a.indexOf(v) === k)
    .map(idx => items.find(i => i.idx === idx) || { ...(state.res.outros || []).find(o => o.idx === idx), idx, fazNome: (state.res.nomesFazendas || {})[((state.res.outros || []).find(o => o.idx === idx) || {}).faz] || '' });
  const marc = (mapa, idx, ri) => (mapa.get(idx) || new Set()).has(ri);
  const linhaPadrao = (it, extra) => `<div class="fixrow ${fix.sel === 'e' + it.idx ? 'sel' : ''}" data-sel="e${it.idx}">${extra}</div>`;
  let h = `<div class="explain"><h3>2. Corrigir os erros da verificação</h3>
    <p>Escolha quais erros o site deve consertar. O que não estiver marcado fica como está. As <b>possíveis estradas</b> não aparecem aqui porque não são erro.</p>
    <p class="note">Clique num talhão para ver no mapa: verde é o que vai ser preenchido, vermelho é o que vai ser apagado.</p></div>`;
  h += `<div class="card"><h4>Buracos (${fmtInt(comBuraco.reduce((s, i) => s + errHoles(i).length, 0))} em ${fmtInt(comBuraco.length)} talhões)</h4>
    <p class="note">Preencher um buraco soma a área dele ao talhão. Marque só se aquela área deveria fazer parte do talhão. Se for mata, APP, sede ou lagoa, deixe sem marcar.</p>
    <div class="sumline"><span>${fmtInt([...fix.buracos.values()].reduce((s, v) => s + v.size, 0))} buracos marcados para preencher</span><span class="btns">
      <button type="button" class="btn ghost small" data-act="burTodos">Marcar todos</button><button type="button" class="btn ghost small" data-act="burNenhum">Desmarcar</button></span></div>
    <div class="fixlist" style="max-height:360px">${comBuraco.length ? porFazenda(comBuraco, it => {
      const hs = errHoles(it), todos = hs.every(x => marc(fix.buracos, it.idx, x.ri));
      return linhaPadrao(it, `<input type="checkbox" data-bur-tal="${it.idx}" ${todos ? 'checked' : ''} aria-label="Preencher todos os buracos do talhão ${esc(it.tal)}">
        <div><span class="t">Talhão ${esc(it.tal)}</span> <span class="s">${hs.length} ${hs.length > 1 ? 'buracos' : 'buraco'}</span></div>
        <div class="num">${fmt(hs.reduce((s, x) => s + x.ha, 0))} ha</div>
        <div class="opts">${hs.map((x, k) => `<label><input type="checkbox" data-bur="${it.idx}:${x.ri}" ${marc(fix.buracos, it.idx, x.ri) ? 'checked' : ''}> ${k + 1} · ${fmt(x.ha)} ha</label>`).join('')}</div>`);
    }) : '<p class="note">Nenhum buraco.</p>'}</div></div>`;
  h += `<div class="card"><h4>Partes soltas (${fmtInt(comParte.reduce((s, i) => s + errExtra(i).length, 0))} em ${fmtInt(comParte.length)} talhões)</h4>
    <p class="note">Apagar remove esse pedaço do talhão. Marque só se ele não deveria estar ali (um pedaço esquecido ou duplicado). Se for parte de verdade do talhão, deixe sem marcar.</p>
    <div class="sumline"><span>${fmtInt([...fix.partes.values()].reduce((s, v) => s + v.size, 0))} partes marcadas para apagar</span><span class="btns">
      <button type="button" class="btn ghost small" data-act="parTodos">Marcar todos</button><button type="button" class="btn ghost small" data-act="parNenhum">Desmarcar</button></span></div>
    <div class="fixlist" style="max-height:360px">${comParte.length ? porFazenda(comParte, it => {
      const ps = errExtra(it), todos = ps.every(x => marc(fix.partes, it.idx, x.ri));
      return linhaPadrao(it, `<input type="checkbox" data-par-tal="${it.idx}" ${todos ? 'checked' : ''} aria-label="Apagar todas as partes soltas do talhão ${esc(it.tal)}">
        <div><span class="t">Talhão ${esc(it.tal)}</span> <span class="s">${ps.length} ${ps.length > 1 ? 'partes soltas' : 'parte solta'}</span></div>
        <div class="num">${fmt(ps.reduce((s, x) => s + x.ha, 0))} ha</div>
        <div class="opts">${ps.map((x, k) => `<label><input type="checkbox" data-par="${it.idx}:${x.ri}" ${marc(fix.partes, it.idx, x.ri) ? 'checked' : ''}> ${String.fromCharCode(65 + (k % 26))} · ${fmt(x.ha)} ha</label>`).join('')}</div>`);
    }) : '<p class="note">Nenhuma parte solta.</p>'}</div></div>`;
  h += `<div class="card"><h4>Desenhos com problema (${fmtInt(invalidos.length)})</h4>
    <p class="note">Registros com o contorno no sentido errado podem ser regravados certos. Registros cujo desenho não tem área (um risco ou um ponto) não têm conserto: a opção é apagar o registro.</p>
    <div class="fixlist" style="max-height:300px">${invalidos.length ? invalidos.map(it => semArea.has(it.idx)
      ? linhaPadrao(it, `<input type="checkbox" data-rem="${it.idx}" ${fix.remover.has(it.idx) ? 'checked' : ''} aria-label="Apagar registro">
        <div><span class="t">Talhão ${esc(it.tal)}</span> <span class="s">${esc(rotulo(it))}</span><div class="badges"><span class="badge bad">Desenho sem área: apagar o registro</span></div></div><div class="num"></div>`)
      : linhaPadrao(it, `<input type="checkbox" data-inv="${it.idx}" ${fix.invertidos.has(it.idx) ? 'checked' : ''} aria-label="Corrigir sentido">
        <div><span class="t">Talhão ${esc(it.tal)}</span> <span class="s">${esc(rotulo(it))}</span><div class="badges"><span class="badge warn">Corrigir o sentido do contorno</span></div></div><div class="num"></div>`)).join('') : '<p class="note">Nenhum.</p>'}</div></div>`;
  return h;
}

function verErro(idx) {
  const it = state.res.items.find(i => i.idx === idx) || (state.res.outros || []).find(o => o.idx === idx);
  if (!it || !fixLayer) return;
  detalhe('e' + idx, `Talhão ${it.tal}: o que muda`);
  legenda(`<b>Talhão ${esc(it.tal)}</b>` + chave(FIXCOR.preenche, 'Buraco que vai ser preenchido', '', .6) + chave(FIXCOR.antes, 'Buraco que fica', '', .2)
    + chave(FIXCOR.apaga, 'Parte que vai ser apagada', '', .6) + chave(FIXCOR.b, 'Parte solta que fica', '', .2));
  fixLayer.clearLayers();
  const outers = it.outers || it.r || [];
  const lim = L.featureGroup().addTo(fixLayer);
  outers.forEach(r => L.polygon(ll(r), { color: FIXCOR.talhao, weight: 2, fillOpacity: .05 }).addTo(lim));
  let txt = [];
  if (it.holes) {
    errHoles(it).forEach((x, k) => {
      const sim = (fix.buracos.get(idx) || new Set()).has(x.ri);
      L.polygon(ll(x.r), { color: sim ? FIXCOR.preenche : FIXCOR.antes, weight: 2, fillColor: sim ? FIXCOR.preenche : FIXCOR.antes, fillOpacity: sim ? .55 : .2, dashArray: sim ? null : '4 3' })
        .bindTooltip(`Buraco ${k + 1}: ${sim ? 'vai ser preenchido' : 'fica como está'}`).addTo(lim);
    });
    errExtra(it).forEach((x, k) => {
      const sim = (fix.partes.get(idx) || new Set()).has(x.ri);
      L.polygon(ll(x.r), { color: sim ? FIXCOR.apaga : FIXCOR.b, weight: 2.5, fillColor: sim ? FIXCOR.apaga : FIXCOR.b, fillOpacity: sim ? .55 : .2, dashArray: sim ? '6 4' : null })
        .bindTooltip(`Parte ${String.fromCharCode(65 + (k % 26))}: ${sim ? 'vai ser apagada' : 'fica como está'}`).addTo(lim);
    });
    const nb = (fix.buracos.get(idx) || new Set()).size, np = (fix.partes.get(idx) || new Set()).size;
    if (nb) txt.push(`${nb} ${nb > 1 ? 'buracos' : 'buraco'} para preencher (verde)`);
    if (np) txt.push(`${np} ${np > 1 ? 'partes' : 'parte'} para apagar (vermelho)`);
  }
  if (lim.getLayers().length) enquadrar(lim.getBounds(), 30);
  $('fixCap').innerHTML = `<b>Talhão ${esc(it.tal)}</b> · ${esc(rotulo({ faz: it.faz, fazNome: it.fazNome || (state.res.nomesFazendas || {})[it.faz] }))}<br>${txt.length ? txt.join(' · ') : 'Nada marcado neste talhão ainda.'} Passe o mouse sobre cada área para ver o que acontece com ela.`;
}

// ---------- etapa 3 ----------
function etapaRevisao() {
  let h = `<div class="explain"><h3>3. Revisar e baixar</h3>
    <p>Confira tudo o que você aprovou. Só isso vai mudar; o resto do arquivo, inclusive a tabela de atributos, fica igual ao original.</p></div>`;
  if (fix.busy) return h + carregando(fix.busy);
  if (!fix.resumo) return h + `<button type="button" class="btn" data-act="calcResumo">Montar o resumo</button>`;
  const r = fix.resumo;
  if (!r.alterados) return h + `<div class="loading">Você ainda não aprovou nenhuma mudança, então o arquivo sairia igual ao original. Volte às etapas anteriores e marque o que quer corrigir.</div>`;
  h += `<div class="statgrid">
      <div class="stat main"><span class="v">${fmtInt(r.alterados)}</span><span class="k">talhões alterados</span></div>
      <div class="stat"><span class="v">${fmtInt(r.removidos)}</span><span class="k">registros apagados</span></div>
      <div class="stat"><span class="v" style="font-size:24px">${fmtInt(r.nvAntes)} → ${fmtInt(r.nvDepois)}</span><span class="k">vértices no arquivo (${fmtPct((r.nvDepois - r.nvAntes) / r.nvAntes * 100, 0)})</span></div>
    </div>
    <div class="card"><h4>Área dos talhões alterados</h4>
      <label class="radios" style="flex-direction:row"><input type="checkbox" id="fxRecalc" ${fix.recalc ? 'checked' : ''}> Recalcular ${r.camposArea.length ? 'os campos <b>' + r.camposArea.map(esc).join('</b> e <b>') + '</b>' : 'a área'} dos talhões alterados</label>
      <p class="note">Sem marcar, a tabela de atributos fica exatamente como estava, mesmo que a área do desenho tenha mudado. Marcando, o valor novo é gravado no mesmo formato e unidade do original.</p></div>
    <div class="card"><h4>O que vai mudar (${fmtInt(r.lista.length)} talhões)</h4>
      <div class="tablewrap"><table class="changes"><thead><tr><th>Fazenda</th><th>Talhão</th><th>Mudanças</th><th>Área antes</th><th>Área depois</th></tr></thead><tbody>
      ${r.lista.map(x => `<tr data-sel="r${x.idx}" class="${fix.sel === 'r' + x.idx ? 'sel' : ''}"><td>${esc(rotulo(x))}</td><td>${esc(x.tal)}</td><td>${x.mudancas.map(esc).join('<br>')}</td><td class="n">${fmt(x.ha0)} ha</td><td class="n">${fmt(x.ha1)} ha</td></tr>`).join('')}
      </tbody></table></div></div>
    <div class="card"><h4>Baixar</h4>
      <p>O arquivo sai com o nome <b>${esc(r.nomeArquivo)}</b>. Dentro dele vão o shapefile corrigido e o <b>relatorio_alteracoes.csv</b>, com tudo o que mudou.</p>
      <div><button type="button" class="btn" data-act="baixar">Gerar e baixar ${esc(r.nomeArquivo)}</button></div>
      ${fix.baixado ? `<div class="okbox">Arquivo baixado. Para conferir, envie o ${esc(r.nomeArquivo)} no quadro lá em cima: a verificação roda de novo no arquivo novo.</div>` : ''}</div>`;
  return h;
}

async function baixar() {
  await carregar('Gerando o arquivo…', async () => {
    const resp = await fixApi('baixar', { plano: fixPlano() });
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = fix.resumo.nomeArquivo;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    fix.baixado = true;
    toast('Arquivo baixado');
  });
}

// ---------- ações ----------
function irPara(n) {
  fix.step = n; fix.sel = null; fix.erro = '';
  if (fixLayer) fixLayer.clearLayers();
  $('fixCap').textContent = 'Clique num item da lista para ver ele aqui.';
  fixRender();
  $('fixSteps').scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (n === 3 && !fix.resumo) calcResumo();
}
const calcResumo = () => carregar('Montando o resumo…', async () => {
  fix.resumo = await (await fixApi('resumo', { plano: fixPlano() })).json();
});
function alternar(mapa, idx, ri, sim) {
  if (!mapa.has(idx)) mapa.set(idx, new Set());
  sim ? mapa.get(idx).add(ri) : mapa.get(idx).delete(ri);
}

$('fixSteps').addEventListener('click', e => { const b = e.target.closest('[data-step]'); if (b) irPara(+b.dataset.step); });
$('fixMain').addEventListener('click', e => {
  const b = e.target.closest('[data-act]');
  if (b) {
    const act = b.dataset.act, items = state.res.items;
    if (act === 'avancar') return irPara(fix.step + 1);
    if (act === 'voltar') return irPara(fix.step - 1);
    if (act === 'calcSimp') return carregar('Calculando a simplificação…', async () => {
      fix.simp = await (await fixApi('simplificar', { tolerancia: fix.tol, escopo: fix.escopo })).json();
      fix.aprovSimp = new Set([...fix.aprovSimp].filter(i => fix.simp.talhoes.some(t => t.idx === i)));
      invalidar();
    });
    if (act === 'simpTodos') { fix.aprovSimp = new Set(fix.simp.talhoes.filter(t => t.recomendado).map(t => t.idx)); invalidar(); }
    if (act === 'simpNenhum') { fix.aprovSimp.clear(); invalidar(); }
    if (act === 'burTodos') { items.forEach(i => errHoles(i).forEach(x => alternar(fix.buracos, i.idx, x.ri, true))); invalidar(); }
    if (act === 'burNenhum') { fix.buracos.clear(); invalidar(); }
    if (act === 'parTodos') { items.forEach(i => errExtra(i).forEach(x => alternar(fix.partes, i.idx, x.ri, true))); invalidar(); }
    if (act === 'parNenhum') { fix.partes.clear(); invalidar(); }
    if (act === 'calcResumo') return calcResumo();
    if (act === 'baixar') return baixar();
    const y = window.scrollY; fixRender(); window.scrollTo(0, y);
    return;
  }
  if (e.target.closest('input, label')) return;  // clique na caixinha não troca o desenho
  const row = e.target.closest('[data-sel]');
  if (!row) return;
  selecionar(row.dataset.sel);
});
$('fixGeral').onclick = voltarGeral;
$('fixAmpliar').onclick = () => {
  const largo = $('fixGrid').classList.toggle('wide');
  $('fixAmpliar').setAttribute('aria-pressed', largo);
  $('fixAmpliar').textContent = largo ? 'Diminuir mapa' : 'Ampliar mapa';
  if (fixMap) setTimeout(() => fixMap.invalidateSize(), 60);
};
$('fixMain').addEventListener('change', e => {
  const t = e.target, d = t.dataset;
  if (t.id === 'fxTol') { fix.tol = +t.value; fix.simp = null; fix.aprovSimp.clear(); invalidar(); return fixRender(); }
  if (t.name === 'fxEsc') { fix.escopo = t.value; fix.simp = null; fix.aprovSimp.clear(); invalidar(); return fixRender(); }
  if (t.id === 'fxRecalc') { fix.recalc = t.checked; fix.baixado = false; return; }
  const items = state.res.items, it = idx => items.find(i => i.idx === idx);
  if (d.simp) { t.checked ? fix.aprovSimp.add(+d.simp) : fix.aprovSimp.delete(+d.simp); invalidar(); }
  else if (d.burTal) { errHoles(it(+d.burTal)).forEach(x => alternar(fix.buracos, +d.burTal, x.ri, t.checked)); invalidar(); }
  else if (d.bur) { const [i, ri] = d.bur.split(':').map(Number); alternar(fix.buracos, i, ri, t.checked); invalidar(); }
  else if (d.parTal) { errExtra(it(+d.parTal)).forEach(x => alternar(fix.partes, +d.parTal, x.ri, t.checked)); invalidar(); }
  else if (d.par) { const [i, ri] = d.par.split(':').map(Number); alternar(fix.partes, i, ri, t.checked); invalidar(); }
  else if (d.inv) { t.checked ? fix.invertidos.add(+d.inv) : fix.invertidos.delete(+d.inv); invalidar(); }
  else if (d.rem) { t.checked ? fix.remover.add(+d.rem) : fix.remover.delete(+d.rem); invalidar(); }
  else return;
  // redesenha só o que depende da escolha, sem pular a lista para o topo
  const listas = [...document.querySelectorAll('#fixMain .fixlist')].map(l => l.scrollTop), y = window.scrollY;
  fixRender();
  document.querySelectorAll('#fixMain .fixlist').forEach((l, k) => { l.scrollTop = listas[k] || 0; });
  window.scrollTo(0, y);
  if (fix.sel && fix.sel[0] === 'e') verErro(+fix.sel.slice(1));
});

fixReset();
