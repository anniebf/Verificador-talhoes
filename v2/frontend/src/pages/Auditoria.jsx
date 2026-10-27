// Etapa 1: tela principal do lote. Atributos à esquerda, mapa no centro, inconsistências à direita.
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, area, data, num } from '../api.js'
import Mapa, { ESTILOS } from '../components/Mapa.jsx'
import { Aviso, Etapas, nomeFazenda, SEVERIDADE, SoltarArquivos } from '../components/Comuns.jsx'

const TODAS = '*'

export default function Auditoria() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const nav = useNavigate()
  const [lote, setLote] = useState(null)
  const [mapa, setMapa] = useState(null)
  const [incs, setIncs] = useState([])
  const [tipo, setTipo] = useState(null)
  const [sel, setSel] = useState(null)          // inconsistência selecionada
  const [talhao, setTalhao] = useState(null)    // talhão clicado no mapa
  const [busca, setBusca] = useState('')
  const [erro, setErro] = useState('')
  const [mesclar, setMesclar] = useState(false)
  const [arquivos, setArquivos] = useState([])
  const [ocupado, setOcupado] = useState('')

  const carregarLote = () => api.get(`/api/lotes/${id}`).then(setLote).catch((e) => setErro(e.message))
  useEffect(() => { carregarLote() }, [id])

  // fazenda escolhida (padrão: a primeira com erro)
  const faz = useMemo(() => {
    if (!lote) return undefined
    if (sp.has('fazenda')) return sp.get('fazenda')
    return (lote.fazendas.find((f) => f.nErros) || lote.fazendas[0])?.codigo ?? TODAS
  }, [lote, sp])
  const fazInfo = lote?.fazendas.find((f) => f.codigo === faz)

  useEffect(() => {
    if (faz === undefined) return
    setSel(null); setTalhao(null); setTipo(null); setMapa(null)
    const p = faz === TODAS ? {} : { fazenda: faz }
    api.get(`/api/lotes/${id}/mapa`, p).then(setMapa).catch((e) => setErro(e.message))
    api.get(`/api/lotes/${id}/inconsistencias`, p).then(setIncs).catch((e) => setErro(e.message))
  }, [id, faz, lote?.versaoAtual, lote?.totais.nErros])

  const escolher = (codigo) => setSp({ fazenda: codigo })
  const filtradas = tipo ? incs.filter((i) => i.tipo === tipo) : incs
  const contagem = useMemo(() => incs.reduce((c, i) => ({ ...c, [i.tipo]: (c[i.tipo] || 0) + 1 }), {}), [incs])
  const idsSel = new Set(sel ? sel.feicoes.map((f) => f.id) : talhao ? [talhao.id] : [])

  const camadas = useMemo(() => [
    mapa && {
      dados: mapa,
      estilo: (f) => idsSel.has(f.properties.id) ? ESTILOS.selecionado : f.properties.erros ? ESTILOS.comErro : ESTILOS.talhao,
      dica: (f) => `Talhão ${f.properties.talhao || '?'}${f.properties.aglomerado ? ` · ${f.properties.aglomerado}` : ''} · ${num(f.properties.areaHa)} ha`
        + (f.properties.erros ? ` · ${f.properties.erros} erro(s)` : ''),
      aoClicar: (f) => { setSel(null); setTalhao(f.properties) },
    },
    { dados: { type: 'FeatureCollection', features: filtradas.filter((i) => i.destaque).map((i) => ({ type: 'Feature', geometry: i.destaque, properties: i })) },
      estilo: (f) => f.properties.id === sel?.id ? { ...ESTILOS.erro, color: '#fff', weight: 3 } : ESTILOS.erro,
      dica: (f) => `${f.properties.nome}: ${f.properties.descricao}`,
      aoClicar: (f) => { setTalhao(null); setSel(f.properties) } },
  ].filter(Boolean), [mapa, filtradas, sel, talhao])

  const enquadrar = sel
    ? (sel.destaque || { type: 'FeatureCollection', features: mapa?.features.filter((f) => idsSel.has(f.properties.id)) || [] })
    : mapa

  const enviarMescla = async () => {
    setOcupado('mesclando'); setErro('')
    const fd = new FormData()
    arquivos.forEach((f) => fd.append('arquivos', f))
    try {
      setLote(await api.post(`/api/lotes/${id}/camadas`, fd))
      setArquivos([]); setMesclar(false)
    } catch (e) { setErro(e.message) } finally { setOcupado('') }
  }

  const reauditar = async () => {
    setOcupado('auditando'); setErro('')
    try { setLote(await api.post(`/api/lotes/${id}/auditar`)) } catch (e) { setErro(e.message) } finally { setOcupado('') }
  }

  const exportar = async (formato) => {
    setOcupado('exportando')
    try { await api.baixar(`/api/lotes/${id}/exportar`, { formato, fazenda: faz === TODAS ? undefined : faz }) }
    catch (e) { setErro(e.message) } finally { setOcupado('') }
  }

  if (!lote) return <div className="pagina"><Aviso>{erro}</Aviso>{!erro && <p className="sub">Carregando lote…</p>}</div>

  const fazendasFiltradas = lote.fazendas.filter((f) => !busca || nomeFazenda(f).toLowerCase().includes(busca.toLowerCase()))
  const revisar = (extra = {}) => {
    const q = new URLSearchParams({ fazenda: faz, ...extra })
    nav(`/lotes/${id}/revisao?${q}`)
  }

  return (
    <div className="tela-lote">
      <div className="barra-lote">
        <div>
          <Link to="/" className="btn-link">← Lotes</Link>
          <h1>{lote.nome}</h1>
          <span className="sub">v{lote.versaoAtual} · {lote.crs}{lote.crsMetrico ? ` (medidas em ${lote.crsMetrico})` : ''}</span>
        </div>
        <Etapas atual={1} loteId={id} />
      </div>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>

      <div className="tres-colunas">
        {/* ---------- painel esquerdo: atributos e metadados ---------- */}
        <aside className="painel">
          <section>
            <h3>Resumo do lote</h3>
            <div className="numeros">
              <div><b>{lote.totais.fazendas}</b><span>fazendas</span></div>
              <div><b>{lote.totais.talhoes}</b><span>talhões</span></div>
              <div><b>{lote.totais.poligonos}</b><span>polígonos</span></div>
            </div>
            <p className="campos">
              Campos: fazenda <code>{lote.campos.fazenda || '—'}</code> · aglomerado <code>{lote.campos.aglomerado || '—'}</code> ·
              talhão <code>{lote.campos.talhao || '—'}</code>
            </p>
          </section>

          <section>
            <div className="titulo-secao">
              <h3>Camadas ({lote.camadas.length})</h3>
              <button className="btn pequeno" onClick={() => setMesclar(!mesclar)}>{mesclar ? 'Cancelar' : '+ Mesclar shapefile'}</button>
            </div>
            {mesclar && (
              <div className="mesclar">
                <SoltarArquivos compacto arquivos={arquivos} setArquivos={setArquivos} />
                <button className="btn primario largo" disabled={!arquivos.length || !!ocupado} onClick={enviarMescla}>
                  {ocupado === 'mesclando' ? 'Mesclando e auditando…' : 'Mesclar no lote'}
                </button>
              </div>
            )}
            <ul className="lista-simples">
              {lote.camadas.map((c) => <li key={c.id}><span>{c.nome}</span><small>{c.feicoes} feições · {data(c.criadoEm)}</small></li>)}
            </ul>
          </section>

          <section className="cresce">
            <h3>Fazendas</h3>
            <input className="busca" placeholder="Buscar fazenda…" value={busca} onChange={(e) => setBusca(e.target.value)} />
            <ul className="lista-fazendas">
              <li className={faz === TODAS ? 'ativo' : ''} onClick={() => escolher(TODAS)}>
                <span>Todas as fazendas</span><small>{lote.totais.talhoes} talhões</small>
                {lote.totais.nErros > 0 && <span className="badge erro">{lote.totais.nErros}</span>}
              </li>
              {fazendasFiltradas.map((f) => (
                <li key={f.codigo} className={faz === f.codigo ? 'ativo' : ''} onClick={() => escolher(f.codigo)}>
                  <span>{nomeFazenda(f)}</span>
                  <small>{f.aglomerados.length} aglom. · {f.talhoes} talhões</small>
                  {f.nErros > 0 ? <span className="badge erro">{f.nErros}</span> : <span className="badge ok">✓</span>}
                </li>
              ))}
            </ul>
          </section>

          {fazInfo && (
            <section>
              <h3>{nomeFazenda(fazInfo)}</h3>
              <div className="numeros">
                <div><b>{fazInfo.talhoes}</b><span>talhões</span></div>
                <div><b>{fazInfo.poligonos}</b><span>polígonos</span></div>
                <div><b>{num(fazInfo.areaHa, 1)}</b><span>ha</span></div>
              </div>
              <table className="tabela compacta">
                <thead><tr><th>Aglomerado</th><th>Talhões</th></tr></thead>
                <tbody>{fazInfo.aglomerados.map((a) => <tr key={a.nome}><td>{a.nome || '—'}</td><td>{a.talhoes}</td></tr>)}</tbody>
              </table>
            </section>
          )}

          {talhao && (
            <section className="destaque-secao">
              <div className="titulo-secao"><h3>Talhão {talhao.talhao || '?'}</h3><button className="btn-link" onClick={() => setTalhao(null)}>fechar</button></div>
              <dl className="dl">
                <dt>Fazenda</dt><dd>{talhao.fazenda}{talhao.fazendaNome ? ` · ${talhao.fazendaNome}` : ''}</dd>
                <dt>Aglomerado</dt><dd>{talhao.aglomerado || '—'}</dd>
                <dt>Área</dt><dd>{num(talhao.areaHa, 4)} ha</dd>
                <dt>Vértices</dt><dd>{talhao.vertices}</dd>
                <dt>Erros</dt><dd>{talhao.erros}</dd>
              </dl>
            </section>
          )}

          <section>
            <h3>Versão atual (v{lote.versaoAtual})</h3>
            <div className="botoes">
              <button className="btn pequeno" disabled={!!ocupado} onClick={() => exportar('shp')}>Baixar shapefile</button>
              <button className="btn pequeno" disabled={!!ocupado} onClick={() => exportar('geojson')}>GeoJSON</button>
              <button className="btn pequeno" disabled={!!ocupado} onClick={reauditar}>{ocupado === 'auditando' ? 'Auditando…' : 'Reauditar'}</button>
            </div>
            <ul className="lista-simples versoes">
              {[...lote.versoes].reverse().map((v) => (
                <li key={v.numero}><span><b>v{v.numero}</b> {v.descricao}</span><small>{v.criadoPor} · {data(v.criadoEm)}</small></li>
              ))}
            </ul>
          </section>
        </aside>

        {/* ---------- mapa central ---------- */}
        <div className="centro">
          <Mapa camadas={camadas} enquadrar={enquadrar} chaveEnquadrar={`${faz}|${sel?.id}|${!!mapa}`}
            rotulo={faz === TODAS ? 'Todas as fazendas (desenho simplificado)' : nomeFazenda(fazInfo)}
            legenda={[{ texto: 'Talhão sem erro', cor: ESTILOS.talhao.color }, { texto: 'Talhão com erro', cor: ESTILOS.comErro.color },
              { texto: 'Trecho com erro', cor: ESTILOS.erro.color, fundo: ESTILOS.erro.fillColor }, { texto: 'Selecionado', cor: ESTILOS.selecionado.color }]} />
          {!mapa && <div className="mapa-carregando">Carregando talhões…</div>}
        </div>

        {/* ---------- painel direito: relatório de inconsistências ---------- */}
        <aside className="painel">
          <section>
            <h3>Relatório de inconsistências</h3>
            <p className="sub">{incs.length} em aberto {faz === TODAS ? 'no lote' : 'nesta fazenda'}</p>
            <ul className="categorias">
              {lote.tipos.map((t) => (
                <li key={t.chave} className={`${tipo === t.chave ? 'ativo' : ''} ${contagem[t.chave] ? '' : 'zerado'}`}
                  onClick={() => contagem[t.chave] && setTipo(tipo === t.chave ? null : t.chave)}>
                  <span className={`sev sev-${t.severidade}`} title={`Severidade ${SEVERIDADE[t.severidade]}`} />
                  <span>{t.nome}</span>
                  <b>{contagem[t.chave] || 0}</b>
                </li>
              ))}
            </ul>
          </section>
          <section className="cresce">
            <h3>{tipo ? lote.tipos.find((t) => t.chave === tipo).nome : 'Todas'} ({filtradas.length})</h3>
            <ul className="lista-incs">
              {filtradas.map((i) => (
                <li key={i.id} className={sel?.id === i.id ? 'ativo' : ''} onClick={() => { setTalhao(null); setSel(i) }}>
                  <div className="inc-topo"><span className={`sev sev-${i.severidade}`} /><b>{i.nome}</b>
                    {i.status !== 'pendente' && <span className={`etiqueta st-${i.status}`}>{i.status === 'aceita' ? 'aceitar' : 'manter'}</span>}
                  </div>
                  <div className="inc-desc">{i.descricao}</div>
                  {i.areaM2 > 0 && <small>{area(i.areaM2)}</small>}
                  {sel?.id === i.id && (
                    <button className="btn pequeno" onClick={(e) => { e.stopPropagation(); revisar({ inc: i.id }) }}>Revisar só este erro →</button>
                  )}
                </li>
              ))}
              {!filtradas.length && <li className="vazio">Nenhuma inconsistência em aberto. ✓</li>}
            </ul>
          </section>
          <div className="rodape-painel">
            <button className="btn primario largo" disabled={!filtradas.length}
              onClick={() => revisar(tipo ? { tipo } : {})}>
              Verificar / corrigir erros detectados ({filtradas.length})
            </button>
          </div>
        </aside>
      </div>
    </div>
  )
}
