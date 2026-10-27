// Etapa 2: revisão das ocorrências. Original x prévia da correção lado a lado (a prévia é calculada numa
// cópia: a base não muda aqui), detalhes da feição e a decisão: aprovar, rejeitar ou deixar pendente.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, area, data, num } from '../api.js'
import Mapa, { ESTILOS, sincronizar } from '../components/Mapa.jsx'
import { Aviso, Classe, Dica, Estado, Etapas, nomeFazenda, SEVERIDADE, STATUS } from '../components/Comuns.jsx'

const FC = (features) => ({ type: 'FeatureCollection', features })

// Vértices dos polígonos como pontos (excesso de vértices). O total conta como o servidor (o ponto que fecha
// cada anel entra na conta), mas o ponto repetido do fechamento não é desenhado duas vezes.
function vertices(fc) {
  const pts = []
  let total = 0
  for (const f of fc?.features || []) {
    const g = f.geometry
    const poligonos = g?.type === 'Polygon' ? [g.coordinates] : g?.type === 'MultiPolygon' ? g.coordinates : []
    for (const anel of poligonos.flat()) { total += anel.length; pts.push(...anel.slice(0, -1)) }
  }
  return { total, dados: FC([{ type: 'Feature', geometry: { type: 'MultiPoint', coordinates: pts }, properties: {} }]) }
}
const SEM_DECISAO = ['identificada', 'em_revisao']
const DECIDIVEL = (i) => i.status !== 'corrigida'
const ROTULO_CARAC = {
  area_ha: 'Área (ha)', area_m2: 'Área (m²)', perimetro_m: 'Perímetro (m)', largura_media_m: 'Largura média (m)', largura_m: 'Largura (m)',
  comprimento_m: 'Comprimento (m)', percentual_do_talhao: '% do talhão', partes: 'Partes', area_parte_ha: 'Área da parte (ha)',
  distancia_ao_principal_m: 'Distância ao contorno principal (m)', vertices: 'Vértices', vertices_depois: 'Vértices depois',
  espacamento_medio_m: 'Espaçamento médio (m)', variacao_area_pct: 'Variação de área (%)', percentual_alvo: '% do talhão recortado',
  percentual_outro: '% do outro talhão', fazendas: 'Fazendas', vizinhos: 'Vizinhos', repetidos: 'Vértices repetidos',
  motivo_geos: 'Motivo (GEOS)', partes_apos_reparo: 'Partes após o reparo', area_apos_reparo_ha: 'Área após o reparo (ha)',
  comprimento_dentro_m: 'Comprimento dentro do talhão (m)', largura_faixa_m: 'Largura da faixa (m)', onde: 'Onde', distancia_m: 'Distância (m)',
  registros: 'Registros', fechado: 'Circuito fechado', comprimento_escape_m: 'Comprimento do escape (m)',
}

export default function Revisao() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const nav = useNavigate()
  const fazenda = sp.get('fazenda') ?? '*'
  const unico = sp.get('inc') ? Number(sp.get('inc')) : null
  const [filtro, setFiltro] = useState({ tipo: sp.get('tipo') || '', status: 'abertas', talhao: '', auto: '' })
  const [lista, setLista] = useState(null)
  const [lote, setLote] = useState(null)
  const [verFiltros, setVerFiltros] = useState(false)
  const [aba, setAba] = useState('resumo')
  const [atualId, setAtualId] = useState(null)
  const [det, setDet] = useState(null)
  const [marcadas, setMarcadas] = useState(new Set())
  const [erro, setErro] = useState('')
  const [info, setInfo] = useState('')
  const [salvando, setSalvando] = useState(false)
  const mapas = useRef([])

  useEffect(() => { api.get(`/api/lotes/${id}`).then(setLote).catch((e) => setErro(e.message)) }, [id])
  const tipos = lote?.tipos || []
  // a revisão é sempre de uma fazenda: sem fazenda escolhida, abre a que tem mais ocorrências
  useEffect(() => {
    if (!lote || unico || fazenda !== '*') return
    const f = [...lote.fazendas].filter((x) => x.nErros).sort((a, b) => (!a.codigo) - (!b.codigo) || b.nErros - a.nErros)[0]
    if (f) setSp({ fazenda: f.codigo }, { replace: true })
  }, [lote, fazenda, unico])
  const fazendasComErro = (lote?.fazendas || []).filter((f) => f.nErros || f.codigo === fazenda)
  const grupos = fazendasComErro.reduce((g, f) => ({ ...g, [f.aglomerado || 'Sem aglomerado']: [...(g[f.aglomerado || 'Sem aglomerado'] || []), f] }), {})

  const carregar = useCallback(() => {
    const p = unico ? { ids: String(unico), status: '' } : {
      fazenda: fazenda === '*' ? undefined : fazenda, tipo: filtro.tipo || undefined, status: filtro.status,
      talhao: filtro.talhao || undefined, auto: filtro.auto === '' ? undefined : filtro.auto === 'sim',
    }
    return api.get(`/api/lotes/${id}/inconsistencias`, p)
      .then((l) => { setLista(l); setMarcadas(new Set()) }).catch((e) => setErro(e.message))
  }, [id, fazenda, unico, filtro])
  useEffect(() => { carregar() }, [carregar])

  useEffect(() => {
    if (!lista?.length) { setAtualId(null); return }
    if (!lista.some((i) => i.id === atualId)) setAtualId((lista.find((i) => SEM_DECISAO.includes(i.status)) || lista[0]).id)
  }, [lista])

  useEffect(() => {
    if (!atualId) { setDet(null); return }
    setDet(null)
    setAba('resumo')
    api.get(`/api/lotes/${id}/inconsistencias/${atualId}`).then((d) => {
      setDet(d)
      // abrir a ocorrência a coloca "em revisão"
      setLista((l) => l?.map((i) => (i.id === d.id ? { ...i, status: d.status } : i)))
    }).catch((e) => setErro(e.message))
  }, [id, atualId])

  const aoCriarMapa = useCallback((m) => {
    mapas.current.push(m)
    if (mapas.current.length === 2) sincronizar(mapas.current[0], mapas.current[1])
    return () => { mapas.current = mapas.current.filter((x) => x !== m) }
  }, [])

  const atual = lista?.find((i) => i.id === atualId)
  const pos = lista ? lista.findIndex((i) => i.id === atualId) : -1
  const cont = useMemo(() => (lista || []).reduce((c, i) => ({ ...c, [i.status]: (c[i.status] || 0) + 1 }), {}), [lista])

  const decidir = async (ids, decisao, avancar = true) => {
    setSalvando(true); setErro(''); setInfo('')
    try {
      const r = await api.post(`/api/lotes/${id}/decisoes`, { ids, decisao })
      const por = Object.fromEntries(r.alteradas.map((x) => [x.id, x]))
      const nova = lista.map((i) => (por[i.id] ? { ...i, ...por[i.id] } : i))
      setLista(nova)
      setDet((d) => (d && por[d.id] ? { ...d, ...por[d.id] } : d))
      if (r.recusadas.length) setInfo(`${r.alteradas.length} decidida(s). ${r.recusadas.length} não recebeu(ram) essa decisão: `
        + r.recusadas.map((x) => `#${x.id} (${x.motivo})`).join('; ') + '.')
      setMarcadas(new Set())
      if (avancar && ids.length === 1) {
        const k = nova.findIndex((i) => i.id === ids[0])
        const prox = [...nova.slice(k + 1), ...nova.slice(0, k)].find((i) => SEM_DECISAO.includes(i.status))
        if (prox) setAtualId(prox.id)
      }
    } catch (e) { setErro(e.message) } finally { setSalvando(false) }
  }

  // seleção em lote (3.2.5): mostra quem é elegível para cada decisão
  const sel = (lista || []).filter((i) => marcadas.has(i.id))
  const elegiveis = sel.filter((i) => i.temCorrecao && DECIDIVEL(i))
  const individuais = sel.filter((i) => !i.temCorrecao)
  const marcar = (i) => { const n = new Set(marcadas); n.has(i) ? n.delete(i) : n.add(i); setMarcadas(n) }
  const marcarTodas = () => setMarcadas(marcadas.size === lista.length ? new Set() : new Set(lista.map((i) => i.id)))

  // atalhos: A aprova, R rejeita, P deixa pendente, setas navegam
  useEffect(() => {
    const tecla = (e) => {
      if (!atual || salvando || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || e.ctrlKey || e.metaKey) return
      if (e.key === 'a' && atual.temCorrecao && DECIDIVEL(atual)) decidir([atual.id], 'aprovar')
      else if (e.key === 'r' && DECIDIVEL(atual)) decidir([atual.id], 'rejeitar')
      else if (e.key === 'p' && DECIDIVEL(atual)) decidir([atual.id], 'pendente')
      else if (e.key === 'ArrowDown' && pos < lista.length - 1) { e.preventDefault(); setAtualId(lista[pos + 1].id) }
      else if (e.key === 'ArrowUp' && pos > 0) { e.preventDefault(); setAtualId(lista[pos - 1].id) }
    }
    window.addEventListener('keydown', tecla)
    return () => window.removeEventListener('keydown', tecla)
  })

  const destaque = det?.destaque ? FC([{ type: 'Feature', geometry: det.destaque, properties: {} }]) : null
  const estiloFeicao = (base) => (f) => (f.properties.linha ? { ...ESTILOS.estrada, color: base.color } : base)
  const dicaT = (f) => (f.properties.linha ? `Linha #${f.properties.indice + 1}` : `Talhão ${f.properties.talhao || '?'}${f.properties.aglomerado ? ` · ${f.properties.aglomerado}` : ''}`)
  // excesso de vértices: cada vértice vira um ponto no erro (esquerda) e na solução (direita)
  const vert = useMemo(() => (det?.tipo === 'excesso_vertices'
    ? { antes: vertices(det.original), depois: det.manual ? null : vertices(det.proposta) } : null), [det])
  const [esquerda, direita] = useMemo(() => {
    if (!det) return [[], []]
    const viz = { dados: det.vizinhos, estilo: (f) => (f.properties.linha ? ESTILOS.estrada : ESTILOS.vizinho), dica: (f) => `${dicaT(f)} (vizinho)` }
    return [
      [viz, { dados: det.original, estilo: estiloFeicao(ESTILOS.original), dica: dicaT },
        destaque && { dados: destaque, estilo: ESTILOS.erro, ponto: { fillColor: '#ff1f3d' }, dica: () => det.nome },
        vert && { dados: vert.antes.dados, ponto: { radius: 2.5, weight: 0.5, color: '#000', fillColor: ESTILOS.original.color } }].filter(Boolean),
      [viz, { dados: det.proposta, estilo: estiloFeicao(ESTILOS.proposta), dica: dicaT },
        destaque && { dados: destaque, estilo: { color: '#fff', weight: 1.5, dashArray: '4 4', fillOpacity: 0 }, ponto: { fillColor: '#3ddc84', radius: 5 } },
        vert?.depois && { dados: vert.depois.dados, ponto: { radius: 2.5, weight: 0.5, color: '#000', fillColor: ESTILOS.proposta.color } }].filter(Boolean),
    ]
  }, [det, vert])
  const enquadrar = det ? [det.original, destaque].filter(Boolean) : null

  const aprovadas = cont.aprovada || 0
  const prosseguir = () => nav(`/lotes/${id}/correcao`, { state: { ids: lista.map((i) => i.id), fazenda } })
  const voltar = `/lotes/${id}?fazenda=${encodeURIComponent(fazenda)}`
  const mudaFiltro = (k) => (e) => setFiltro({ ...filtro, [k]: e.target.value })

  if (lista === null) return <div className="pagina"><Aviso>{erro}</Aviso>{!erro && <p className="sub">Carregando…</p>}</div>

  return (
    <div className="tela-lote">
      <div className="barra-lote">
        <div className="barra-titulo">
          <Link to={voltar} className="btn-link">← Voltar ao mapa do lote</Link>
          <h1>Revisão {unico && `da ocorrência #${unico}`}</h1>
        </div>
        <Etapas atual={2} loteId={id} fazenda={fazenda} />
        {!unico && lote && (
          <label className="seletor-fazenda">Fazenda
            <select value={fazenda} onChange={(e) => setSp({ fazenda: e.target.value })}>
              {fazenda === '*' && <option value="*">Escolha…</option>}
              {Object.entries(grupos).sort(([a], [b]) => a.localeCompare(b, 'pt-BR')).map(([g, fs]) => (
                <optgroup key={g} label={g}>
                  {fs.map((f) => <option key={f.codigo} value={f.codigo}>{nomeFazenda(f)} ({f.nErros})</option>)}
                </optgroup>
              ))}
            </select>
          </label>
        )}
      </div>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>
      <Aviso tipo="alerta" aoFechar={() => setInfo('')}>{info}</Aviso>
      <Dica id="revisao">
        Compare a <b>geometria original</b> (esquerda) com a <b>prévia da correção</b> (direita) e decida: <b>A</b> aprovar,
        <b> R</b> rejeitar, <b>P</b> deixar pendente. Aprovar ainda não altera nada: a correção só é aplicada na etapa 3.
      </Dica>

      <div className="revisao">
        <aside className="painel">
          <section>
            {unico ? (
              <p className="sub">Revisando uma ocorrência. <button className="btn-link" onClick={() => { sp.delete('inc'); setSp(sp) }}>Ver todas {fazenda === '*' ? 'do lote' : 'da fazenda'}</button></p>
            ) : (
              <div className="filtros-revisao">
                <div className="linha-busca">
                  <input placeholder="Buscar talhão…" value={filtro.talhao} onChange={mudaFiltro('talhao')} />
                  <button className={`btn pequeno ${verFiltros || filtro.tipo || filtro.status !== 'abertas' || filtro.auto ? 'ativo' : ''}`}
                    onClick={() => setVerFiltros(!verFiltros)}>Filtros {verFiltros ? '▴' : '▾'}</button>
                </div>
                {verFiltros && <>
                <select value={filtro.tipo} onChange={mudaFiltro('tipo')}>
                  <option value="">Todas as categorias</option>
                  {tipos.map((t) => <option key={t.chave} value={t.chave}>{t.grupo} · {t.nome}</option>)}
                </select>
                <select value={filtro.status} onChange={mudaFiltro('status')}>
                  <option value="abertas">Em aberto (todos os estados)</option>
                  {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.texto}</option>)}
                </select>
                <select value={filtro.auto} onChange={mudaFiltro('auto')}>
                  <option value="">Com e sem correção automática</option>
                  <option value="sim">Só com correção automática</option>
                  <option value="nao">Só manuais</option>
                </select>
                </>}
              </div>
            )}
            <p className="contagem-estados">
              {Object.entries(cont).map(([k, n]) => <span key={k}><Estado s={k} /> {n}</span>)}
            </p>
          </section>

          {!unico && lista.length > 0 && (
            <section className="lote-acoes">
              <label className="linha-check"><input type="checkbox" checked={marcadas.size === lista.length} onChange={marcarTodas} />
                Selecionar todas ({lista.length})</label>
              {sel.length > 0 && (
                <>
                  <p className="sub">{sel.length} selecionada(s): <b>{elegiveis.length}</b> elegível(is) para aprovação em lote
                    {individuais.length > 0 && <>; <b>{individuais.length}</b> sem correção automática (precisam de revisão individual)</>}.</p>
                  <div className="botoes">
                    <button className="btn pequeno primario" disabled={salvando || !elegiveis.length}
                      onClick={() => decidir(elegiveis.map((i) => i.id), 'aprovar', false)}>Aprovar elegíveis ({elegiveis.length})</button>
                    <button className="btn pequeno" disabled={salvando} onClick={() => decidir(sel.map((i) => i.id), 'rejeitar', false)}>Rejeitar</button>
                    <button className="btn pequeno" disabled={salvando} onClick={() => decidir(sel.map((i) => i.id), 'pendente', false)}>Deixar pendente</button>
                  </div>
                </>
              )}
            </section>
          )}

          <section className="cresce sem-pad">
            <ul className="lista-incs rolagem">
              {lista.map((i) => (
                <li key={i.id} className={i.id === atualId ? 'ativo' : ''} onClick={() => setAtualId(i.id)}>
                  <div className="inc-topo">
                    {!unico && <input type="checkbox" checked={marcadas.has(i.id)} onClick={(e) => e.stopPropagation()} onChange={() => marcar(i.id)} />}
                    <span className={`sev sev-${i.severidade}`} /><b>{i.nome}</b>
                  </div>
                  <div className="inc-desc">{i.descricao}</div>
                  <div className="inc-rodape"><Estado s={i.status} />{!i.temCorrecao && <small>manual</small>}</div>
                </li>
              ))}
              {!lista.length && <li className="vazio">Nenhuma ocorrência neste filtro.</li>}
            </ul>
          </section>
          <div className="rodape-painel">
            <button className="btn primario largo" disabled={!aprovadas} onClick={prosseguir}
              title={!aprovadas ? 'Aprove pelo menos uma correção para seguir' : ''}>
              Seguir para a correção ({aprovadas} aprovada{aprovadas === 1 ? '' : 's'}) →
            </button>
            <small className="sub">Só as aprovadas serão executadas. Pendentes e rejeitadas continuam registradas.</small>
          </div>
        </aside>

        <div className="comparacao">
          <div className="dois-mapas">
            <Mapa aoCriar={aoCriarMapa} camadas={esquerda} enquadrar={enquadrar} chaveEnquadrar={det?.id}
              rotulo="Geometria original" className="lado-original"
              legenda={[{ texto: 'Feição envolvida', cor: ESTILOS.original.color }, { texto: 'Trecho apontado', cor: ESTILOS.erro.color, fundo: ESTILOS.erro.fillColor },
                { texto: 'Vizinhos', cor: ESTILOS.vizinho.color },
                vert && { texto: `Vértices: ${vert.antes.total.toLocaleString('pt-BR')}`, cor: '#000', fundo: ESTILOS.original.color }].filter(Boolean)} />
            <Mapa aoCriar={aoCriarMapa} camadas={direita} rotulo={det && det.manual ? 'Sem correção automática' : 'Prévia da correção (cópia)'}
              className="lado-proposta"
              legenda={[{ texto: 'Resultado esperado', cor: ESTILOS.proposta.color }, { texto: 'Onde estava o problema', cor: '#fff' },
                vert?.depois && { texto: `Vértices: ${vert.depois.total.toLocaleString('pt-BR')}`, cor: '#000', fundo: ESTILOS.proposta.color }].filter(Boolean)} />
            {atualId && !det && <div className="mapa-carregando">Carregando…</div>}
          </div>

          {det && (
            <div className="decisao">
              <div className="decisao-info">
                <div className="inc-topo">
                  <span className={`sev sev-${det.severidade}`} /><b>#{det.id} · {det.grupo} · {det.nome}</b>
                  <Classe c={det.classificacao} /><Estado s={det.status} />
                  <span className="sub">Severidade {SEVERIDADE[det.severidade]} · {pos + 1} de {lista.length}</span>
                </div>
                <p><b>{det.descricao}</b></p>
                <div className="abas">
                  {[['resumo', 'Resumo'], ['feicoes', `Feições (${det.envolvidas.length})`], ['historico', `Histórico (${det.historico?.length || 0})`]].map(([k, r]) => (
                    <button key={k} className={aba === k ? 'ativo' : ''} onClick={() => setAba(k)}>{r}</button>
                  ))}
                </div>
                {aba === 'resumo' && <div className="detalhe-grade">
                  <div>
                    <h4>Por que foi apontada</h4>
                    <p>{det.motivo}</p>
                    <h4>Ação proposta</h4>
                    <p>{det.manual ? <b className="laranja">Intervenção manual necessária: não há correção automática para este caso.</b> : det.operacao}</p>
                    {det.limitacoes && <><h4>Limitações e riscos</h4><p>{det.limitacoes}</p></>}
                    {det.validacao?.length > 0 && (
                      <><h4>Última validação</h4>
                        <ul className="validacao">{det.validacao.map((v) => <li key={v.nome} className={v.ok ? 'ok' : 'nok'}>{v.ok ? '✓' : '✗'} {v.nome}: {v.detalhe}</li>)}</ul></>
                    )}
                  </div>
                  <div>
                    <h4>Características</h4>
                    <dl className="dl">
                      {Object.entries(det.caracteristicas).map(([k, v]) => (
                        <ItemDl key={k} rot={ROTULO_CARAC[k] || k} v={Array.isArray(v) ? v.join(', ') : typeof v === 'boolean' ? (v ? 'sim' : 'não') : v} />
                      ))}
                    </dl>
                  </div>
                </div>}
                {aba === 'feicoes' && <table className="tabela compacta">
                  <thead><tr><th>Feição</th><th>Camada / registro</th><th>Fazenda</th><th>Medida antes → depois</th><th>Vértices</th><th>Partes</th><th>Válida</th></tr></thead>
                  <tbody>
                    {det.envolvidas.map((e) => (
                      <tr key={e.id} className={e.alvo ? 'alvo' : ''}>
                        <td>{e.linha ? 'Linha' : `Talhão ${e.talhao || '?'}`}{e.aglomerado ? ` (${e.aglomerado})` : ''} <small>#{e.id}</small>{e.alvo ? ' ✎' : ''}</td>
                        <td>{e.camada} / {e.indiceOrigem + 1}</td>
                        <td>{e.fazenda || '—'}</td>
                        <td>{e.linha ? `${num(e.antes.comprimentoM, 1)} → ${num(e.depois.comprimentoM, 1)} m`
                          : e.removida ? `${num(e.antes.areaHa, 4)} ha → removido` : `${num(e.antes.areaHa, 4)} → ${num(e.depois.areaHa, 4)} ha`}
                          {!e.linha && !e.removida && e.depois.areaHa !== e.antes.areaHa && <small> ({e.depois.areaHa > e.antes.areaHa ? '+' : '−'}{area(Math.abs(e.depois.areaHa - e.antes.areaHa) * 1e4)})</small>}</td>
                        <td>{e.antes.vertices} → {e.depois.vertices}</td>
                        <td>{e.antes.partes} → {e.depois.partes}</td>
                        <td>{e.antes.valida ? 'sim' : 'não'} → {e.depois.valida ? 'sim' : 'não'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>}
                {aba === 'historico' && (
                  <ul className="historico">{(det.historico || []).map((h, k) => <li key={k}>{data(h.dataHora)} · <b>{h.estado}</b> · {h.usuario}{h.execucaoId ? ` · execução #${h.execucaoId}` : ''}{h.nota ? ` — ${h.nota}` : ''}</li>)}</ul>
                )}
              </div>
              <div className="decisao-botoes">
                <div className="nav-setas">
                  <button className="btn pequeno" disabled={pos <= 0} onClick={() => setAtualId(lista[pos - 1].id)}>↑ Anterior</button>
                  <button className="btn pequeno" disabled={pos >= lista.length - 1} onClick={() => setAtualId(lista[pos + 1].id)}>Próxima ↓</button>
                </div>
                <button className={`btn grande primario ${det.status === 'aprovada' ? 'escolhido-aceitar' : ''}`}
                  disabled={salvando || det.manual || !DECIDIVEL(det)} onClick={() => decidir([det.id], 'aprovar')}>
                  Aprovar correção <kbd>A</kbd>
                </button>
                <button className={`btn grande ${det.status === 'rejeitada' ? 'escolhido-manter' : ''}`} disabled={salvando || !DECIDIVEL(det)}
                  onClick={() => decidir([det.id], 'rejeitar')}>
                  Rejeitar correção <kbd>R</kbd>
                </button>
                <button className={`btn ${det.status === 'pendente' ? 'escolhido-manter' : ''}`} disabled={salvando || !DECIDIVEL(det)}
                  onClick={() => decidir([det.id], 'pendente')}>
                  Manter pendente <kbd>P</kbd>
                </button>
                <small className="sub">Aprovar autoriza a correção; ela só conta como <b>corrigida</b> depois de executada e validada na etapa 3.</small>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ItemDl({ rot, v }) {
  return <><dt>{rot}</dt><dd>{v}</dd></>
}
