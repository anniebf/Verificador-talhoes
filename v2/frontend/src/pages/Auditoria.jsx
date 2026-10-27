// Etapa 1: tela principal do lote. Atributos à esquerda, mapa no centro, ocorrências à direita.
// Nada aqui altera a base: a auditoria só lê e registra ocorrências.
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, area, data, num } from '../api.js'
import Mapa, { ESTILOS } from '../components/Mapa.jsx'
import { Aviso, Classe, CLASSIFICACAO, Estado, Etapas, nomeFazenda, SEVERIDADE, SoltarArquivos } from '../components/Comuns.jsx'

const TODAS = '*'
const NIVEIS = [['fazenda', 'Código da fazenda'], ['fazenda_nome', 'Nome da fazenda'], ['aglomerado', 'Aglomerado'],
  ['talhao', 'Talhão'], ['area_ha', 'Área (ha)']]

export default function Auditoria() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const nav = useNavigate()
  const [lote, setLote] = useState(null)
  const [mapa, setMapa] = useState(null)
  const [incs, setIncs] = useState([])
  const [tipo, setTipo] = useState(null)
  const [sel, setSel] = useState(null)          // ocorrência selecionada
  const [talhao, setTalhao] = useState(null)    // feição clicada no mapa
  const [busca, setBusca] = useState('')
  const [erro, setErro] = useState('')
  const [mesclar, setMesclar] = useState(false)
  const [arquivos, setArquivos] = useState([])
  const [ocupado, setOcupado] = useState('')
  const [campos, setCampos] = useState(null)    // edição do mapeamento de colunas

  const carregarLote = () => api.get(`/api/lotes/${id}`).then(setLote).catch((e) => setErro(e.message))
  useEffect(() => { carregarLote() }, [id])

  // fazenda escolhida (padrão: a primeira com ocorrência)
  const faz = useMemo(() => {
    if (!lote) return undefined
    if (sp.has('fazenda')) return sp.get('fazenda')
    return (lote.fazendas.find((f) => f.nErros && f.talhoes) || lote.fazendas[0])?.codigo ?? TODAS
  }, [lote, sp])
  const fazInfo = lote?.fazendas.find((f) => f.codigo === faz)

  useEffect(() => {
    if (faz === undefined) return
    setSel(null); setTalhao(null); setTipo(null); setMapa(null)
    const p = faz === TODAS ? {} : { fazenda: faz }
    api.get(`/api/lotes/${id}/mapa`, p).then(setMapa).catch((e) => setErro(e.message))
    api.get(`/api/lotes/${id}/inconsistencias`, p).then(setIncs).catch((e) => setErro(e.message))
  }, [id, faz, lote?.versaoAtual, lote?.ultimaAuditoria?.id])

  const escolher = (codigo) => setSp({ fazenda: codigo })
  const filtradas = tipo ? incs.filter((i) => i.tipo === tipo) : incs
  const contagem = useMemo(() => incs.reduce((c, i) => ({ ...c, [i.tipo]: (c[i.tipo] || 0) + 1 }), {}), [incs])
  const idsSel = new Set(sel ? sel.feicoes.map((f) => f.id) : talhao ? [talhao.id] : [])

  const camadas = useMemo(() => [
    mapa && {
      dados: mapa,
      estilo: (f) => idsSel.has(f.properties.id) ? ESTILOS.selecionado
        : f.properties.linha ? (f.properties.erros ? ESTILOS.estradaErro : ESTILOS.estrada)
          : f.properties.erros ? ESTILOS.comErro : ESTILOS.talhao,
      dica: (f) => f.properties.linha
        ? `Linha #${f.properties.indice + 1} · ${num(f.properties.comprimentoM, 0)} m` + (f.properties.erros ? ` · ${f.properties.erros} ocorrência(s)` : '')
        : `Talhão ${f.properties.talhao || '?'}${f.properties.aglomerado ? ` · ${f.properties.aglomerado}` : ''} · ${num(f.properties.areaHa)} ha`
          + (f.properties.erros ? ` · ${f.properties.erros} ocorrência(s)` : ''),
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

  const acao = async (qual, f) => {
    setOcupado(qual); setErro('')
    try { await f() } catch (e) { setErro(e.message) } finally { setOcupado('') }
  }
  const enviarMescla = () => acao('mesclando', async () => {
    const fd = new FormData()
    arquivos.forEach((f) => fd.append('arquivos', f))
    setLote(await api.post(`/api/lotes/${id}/camadas`, fd))
    setArquivos([]); setMesclar(false)
  })
  const reauditar = () => acao('auditando', async () => setLote(await api.post(`/api/lotes/${id}/auditar`, {})))
  const salvarCampos = () => acao('campos', async () => { setLote(await api.put(`/api/lotes/${id}/campos`, campos)); setCampos(null) })
  const exportar = (formato) => acao('exportando', () => api.baixar(`/api/lotes/${id}/exportar`, { formato, fazenda: faz === TODAS ? undefined : faz }))
  const relatorio = (formato) => acao('relatorio', () => api.baixar(`/api/execucoes/${lote.ultimaAuditoria.id}/relatorio/${formato}`))

  if (!lote) return <div className="pagina"><Aviso>{erro}</Aviso>{!erro && <p className="sub">Carregando lote…</p>}</div>

  const avisos = lote.camadas.flatMap((c) => c.avisos || [])
  const aud = lote.ultimaAuditoria
  const fazendasFiltradas = lote.fazendas.filter((f) => !busca || nomeFazenda(f).toLowerCase().includes(busca.toLowerCase()))
  const revisar = (extra = {}) => nav(`/lotes/${id}/revisao?${new URLSearchParams({ fazenda: faz, ...extra })}`)

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
      {avisos.length > 0 && (
        <details className="aviso-faixa alerta">
          <summary>{avisos.length} aviso(s) na leitura dos arquivos</summary>
          <ul>{avisos.map((a) => <li key={a}>{a}</li>)}</ul>
        </details>
      )}

      <div className="tres-colunas">
        {/* ---------- painel esquerdo: atributos e metadados ---------- */}
        <aside className="painel">
          <section>
            <h3>Resultado da auditoria {aud && <small className="sub">#{aud.id} · {data(aud.inicio)}</small>}</h3>
            <div className="numeros">
              <div><b>{aud?.resumo.feicoes ?? '—'}</b><span>feições analisadas</span></div>
              <div><b>{lote.totais.nErros}</b><span>ocorrências em aberto</span></div>
              <div><b>{lote.totais.fazendas}</b><span>fazendas</span></div>
            </div>
            <div className="numeros">
              <div><b>{lote.totais.talhoes}</b><span>talhões</span></div>
              <div><b>{lote.totais.poligonos}</b><span>polígonos</span></div>
              <div><b>{lote.totais.linhas}</b><span>linhas</span></div>
            </div>
            <p className="campos">
              {Object.entries(CLASSIFICACAO).map(([k, v]) => <span key={k}>{v}: <b>{lote.totais.porClassificacao[k] || 0}</b> · </span>)}
            </p>
            {aud && (
              <div className="botoes">
                <button className="btn pequeno" disabled={!!ocupado} onClick={() => relatorio('pdf')}>Relatório PDF</button>
                <button className="btn pequeno" disabled={!!ocupado} onClick={() => relatorio('csv')}>CSV</button>
                <button className="btn pequeno" disabled={!!ocupado} onClick={reauditar}>{ocupado === 'auditando' ? 'Auditando…' : 'Reauditar'}</button>
              </div>
            )}
          </section>

          <section>
            <div className="titulo-secao">
              <h3>Campos da hierarquia</h3>
              <button className="btn-link" onClick={() => setCampos({ ...Object.fromEntries(NIVEIS.map(([k]) => [k, lote.campos[k] || ''])) })}>alterar</button>
            </div>
            <p className="campos">
              {NIVEIS.slice(0, 4).map(([k, rot]) => (
                <span key={k}>{rot}: {lote.campos[k] ? <code>{lote.campos[k]}</code> : <b className="laranja">não definido</b>} · </span>
              ))}
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
              {lote.camadas.map((c) => (
                <li key={c.id}><span>{c.nome} <span className="etiqueta">{c.geometria === 'linha' ? 'linhas' : 'polígonos'}</span></span>
                  <small>{c.feicoes} feições · {data(c.criadoEm)}</small></li>
              ))}
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
                  <small>{f.talhoes ? `${f.aglomerados.length} aglom. · ${f.talhoes} talhões` : 'ocorrências de linhas'}</small>
                  {f.nErros > 0 ? <span className="badge erro">{f.nErros}</span> : <span className="badge ok">✓</span>}
                </li>
              ))}
            </ul>
          </section>

          {fazInfo && fazInfo.talhoes > 0 && (
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
              <div className="titulo-secao"><h3>{talhao.linha ? `Linha #${talhao.indice + 1}` : `Talhão ${talhao.talhao || '?'}`}</h3>
                <button className="btn-link" onClick={() => setTalhao(null)}>fechar</button></div>
              <dl className="dl">
                <dt>ID da feição</dt><dd>{talhao.id} (registro {talhao.indice + 1} da camada)</dd>
                <dt>Fazenda</dt><dd>{talhao.fazenda || '—'}{talhao.fazendaNome ? ` · ${talhao.fazendaNome}` : ''}</dd>
                {!talhao.linha && <><dt>Aglomerado</dt><dd>{talhao.aglomerado || '—'}</dd></>}
                <dt>{talhao.linha ? 'Comprimento' : 'Área'}</dt><dd>{talhao.linha ? `${num(talhao.comprimentoM, 0)} m` : `${num(talhao.areaHa, 4)} ha`}</dd>
                <dt>Vértices</dt><dd>{talhao.vertices}</dd>
                <dt>Ocorrências</dt><dd>{talhao.erros}</dd>
              </dl>
            </section>
          )}

          <section>
            <h3>Versões (atual: v{lote.versaoAtual})</h3>
            <div className="botoes">
              <button className="btn pequeno" disabled={!!ocupado} onClick={() => exportar('shp')}>Baixar shapefile</button>
              <button className="btn pequeno" disabled={!!ocupado} onClick={() => exportar('geojson')}>GeoJSON</button>
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
            legenda={[{ texto: 'Talhão sem ocorrência', cor: ESTILOS.talhao.color }, { texto: 'Talhão com ocorrência', cor: ESTILOS.comErro.color },
              { texto: 'Estrada / linha', cor: ESTILOS.estrada.color }, { texto: 'Trecho apontado', cor: ESTILOS.erro.color, fundo: ESTILOS.erro.fillColor },
              { texto: 'Selecionado', cor: ESTILOS.selecionado.color }]} />
          {!mapa && <div className="mapa-carregando">Carregando…</div>}
        </div>

        {/* ---------- painel direito: relatório de ocorrências ---------- */}
        <aside className="painel">
          <section>
            <h3>Ocorrências por categoria</h3>
            <p className="sub">{incs.length} em aberto {faz === TODAS ? 'no lote' : 'nesta fazenda'}</p>
            <ul className="categorias">
              {lote.tipos.map((t) => (
                <li key={t.chave} className={`${tipo === t.chave ? 'ativo' : ''} ${contagem[t.chave] ? '' : 'zerado'}`}
                  onClick={() => contagem[t.chave] && setTipo(tipo === t.chave ? null : t.chave)}>
                  <span className={`sev sev-${t.severidade}`} title={`Severidade ${SEVERIDADE[t.severidade]}`} />
                  <span><small className="grupo">{t.grupo}</small> {t.nome}</span>
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
                  <div className="inc-topo"><span className={`sev sev-${i.severidade}`} /><b>#{i.id} {i.nome}</b><Classe c={i.classificacao} /></div>
                  <div className="inc-desc">{i.descricao}</div>
                  <div className="inc-rodape"><Estado s={i.status} />{i.areaM2 > 0 && <small>{area(i.areaM2)}</small>}
                    <small>{i.temCorrecao ? 'correção automática' : 'manual'}</small></div>
                  {sel?.id === i.id && (
                    <button className="btn pequeno" onClick={(e) => { e.stopPropagation(); revisar({ inc: i.id }) }}>Revisar só esta →</button>
                  )}
                </li>
              ))}
              {!filtradas.length && <li className="vazio">Nenhuma ocorrência em aberto. ✓</li>}
            </ul>
          </section>
          <div className="rodape-painel">
            <button className="btn primario largo" disabled={!filtradas.length} onClick={() => revisar(tipo ? { tipo } : {})}>
              Revisar ocorrências ({filtradas.length}) →
            </button>
          </div>
        </aside>
      </div>

      {campos && (
        <div className="modal-fundo">
          <div className="modal">
            <h2>Campos da hierarquia</h2>
            <p className="sub">Escolha a coluna que identifica cada nível. Um nível sem coluna fica vazio (o sistema não presume nomes).
              Ao salvar, o lote é auditado de novo.</p>
            {NIVEIS.map(([k, rot]) => (
              <label key={k}>{rot}
                <select value={campos[k]} onChange={(e) => setCampos({ ...campos, [k]: e.target.value })}>
                  <option value="">— não definido —</option>
                  {lote.colunas.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
            ))}
            <div className="acoes">
              <button className="btn" onClick={() => setCampos(null)}>Cancelar</button>
              <button className="btn primario" disabled={!!ocupado} onClick={salvarCampos}>{ocupado === 'campos' ? 'Salvando e auditando…' : 'Salvar e reauditar'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
