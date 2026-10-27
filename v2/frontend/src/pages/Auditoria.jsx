// Etapa 1: tela principal do lote. Fazendas (agrupadas por aglomerado) à esquerda, mapa no centro,
// ocorrências da fazenda à direita com rolagem própria. Nada aqui altera a base.
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, area, data, num } from '../api.js'
import Mapa, { ESTILOS } from '../components/Mapa.jsx'
import { Aviso, Dica, Estado, Etapas, ListaFazendas, Menu, nomeFazenda, SoltarArquivos } from '../components/Comuns.jsx'

const TODAS = '*'
const NIVEIS = [['fazenda', 'Código da fazenda'], ['fazenda_nome', 'Nome da fazenda'], ['aglomerado', 'Aglomerado (grupo de fazendas)'],
  ['talhao', 'Talhão'], ['area_ha', 'Área (ha)']]
const FC = (features) => ({ type: 'FeatureCollection', features })

export default function Auditoria() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const nav = useNavigate()
  const [lote, setLote] = useState(null)
  const [mapa, setMapa] = useState(null)
  const [incs, setIncs] = useState([])
  const [tipo, setTipo] = useState(null)
  const [sel, setSel] = useState(null)          // ocorrência selecionada
  const [hover, setHover] = useState(null)      // ocorrência sob o mouse na lista
  const [ficha, setFicha] = useState(null)      // talhão clicado no mapa
  const [erro, setErro] = useState('')
  const [modal, setModal] = useState(null)      // 'campos' | 'mesclar' | 'versoes'
  const [arquivos, setArquivos] = useState([])
  const [campos, setCampos] = useState(null)
  const [ocupado, setOcupado] = useState('')
  const [carregando, setCarregando] = useState(false)

  const carregarLote = () => api.get(`/api/lotes/${id}`).then(setLote).catch((e) => setErro(e.message))
  useEffect(() => { carregarLote() }, [id])

  // fazenda escolhida (padrão: a fazenda com código que tem mais ocorrências)
  const faz = useMemo(() => {
    if (!lote) return undefined
    if (sp.has('fazenda')) return sp.get('fazenda')
    const comCodigo = lote.fazendas.filter((f) => f.codigo && f.talhoes)
    return ([...comCodigo].sort((a, b) => b.nErros - a.nErros)[0] || lote.fazendas[0])?.codigo ?? TODAS
  }, [lote, sp])
  const fazInfo = lote?.fazendas.find((f) => f.codigo === faz)

  useEffect(() => {
    if (faz === undefined) return
    setSel(null); setFicha(null); setTipo(null); setCarregando(true)
    const p = faz === TODAS ? {} : { fazenda: faz }
    Promise.all([api.get(`/api/lotes/${id}/mapa`, p), api.get(`/api/lotes/${id}/inconsistencias`, p)])
      .then(([m, l]) => { setMapa(m); setIncs(l) }).catch((e) => setErro(e.message)).finally(() => setCarregando(false))
  }, [id, faz, lote?.versaoAtual, lote?.ultimaAuditoria?.id])

  const filtradas = tipo ? incs.filter((i) => i.tipo === tipo) : incs
  const contagem = useMemo(() => incs.reduce((c, i) => ({ ...c, [i.tipo]: (c[i.tipo] || 0) + 1 }), {}), [incs])
  const tiposComErro = lote ? lote.tipos.filter((t) => contagem[t.chave]) : []
  const idsSel = useMemo(() => new Set(sel ? sel.feicoes.map((f) => f.id) : ficha ? [ficha.id] : []), [sel, ficha])

  // camadas: os talhões não são recriados ao selecionar (mesmos dados), só repintados
  const destaques = useMemo(() => FC(filtradas.filter((i) => i.destaque).map((i) => ({ type: 'Feature', geometry: i.destaque, properties: i }))), [filtradas])
  const camadas = useMemo(() => [
    mapa && {
      id: 'talhoes', dados: mapa,
      estilo: (f) => idsSel.has(f.properties.id) ? ESTILOS.selecionado
        : f.properties.linha ? (f.properties.erros ? ESTILOS.estradaErro : ESTILOS.estrada)
          : f.properties.erros ? ESTILOS.comErro : ESTILOS.talhao,
      dica: (f) => f.properties.linha ? `Estrada / linha · ${num(f.properties.comprimentoM, 0)} m`
        : `Talhão ${f.properties.talhao || '?'} · ${num(f.properties.areaHa)} ha` + (f.properties.erros ? ` · ${f.properties.erros} ocorrência(s)` : ''),
      aoClicar: (f) => { setSel(null); abrirFicha(f.properties.id) },
    },
    { id: 'erros', dados: destaques,
      estilo: (f) => f.properties.id === sel?.id ? { ...ESTILOS.gravidade[f.properties.severidade], color: '#fff', weight: 3, radius: 8 }
        : ESTILOS.gravidade[f.properties.severidade] || ESTILOS.erro,
      dica: (f) => `${f.properties.nome}: ${f.properties.descricao}`,
      aoClicar: (f) => { setFicha(null); setSel(f.properties) } },
    hover?.destaque && hover.id !== sel?.id && { id: `hover-${hover.id}`, dados: FC([{ type: 'Feature', geometry: hover.destaque, properties: {} }]), estilo: ESTILOS.realce, ponto: { fillColor: '#22d3ee', radius: 9 } },
  ].filter(Boolean), [mapa, destaques, idsSel, sel, hover])

  const enquadrar = sel
    ? (sel.destaque || FC(mapa?.features.filter((f) => idsSel.has(f.properties.id)) || []))
    : mapa

  const abrirFicha = (fid) => api.get(`/api/lotes/${id}/feicoes/${fid}`).then(setFicha).catch((e) => setErro(e.message))
  const acao = async (qual, f) => {
    setOcupado(qual); setErro('')
    try { await f() } catch (e) { setErro(e.message) } finally { setOcupado('') }
  }
  const escolher = (codigo) => setSp({ fazenda: codigo })
  const revisar = (extra = {}) => nav(`/lotes/${id}/revisao?${new URLSearchParams({ fazenda: faz, ...extra })}`)
  const baixar = (url, p) => acao('baixando', () => api.baixar(url, p))

  if (!lote) return <div className="pagina"><Aviso>{erro}</Aviso>{!erro && <p className="sub">Carregando lote…</p>}</div>

  const avisos = lote.camadas.flatMap((c) => c.avisos || [])
  const aud = lote.ultimaAuditoria
  const semCampos = !lote.campos.fazenda || !lote.campos.talhao
  const titulo = faz === TODAS ? 'Visão geral do lote' : nomeFazenda(fazInfo) || `Fazenda ${faz}`

  return (
    <div className="tela-lote">
      <div className="barra-lote">
        <div className="barra-titulo">
          <Link to="/" className="btn-link">← Lotes</Link>
          <h1>{lote.nome}</h1>
          <p className="resumo-chips">
            <span><b>{lote.totais.talhoes.toLocaleString('pt-BR')}</b> talhões</span>
            <span><b>{lote.totais.fazendas}</b> fazendas</span>
            {lote.totais.linhas > 0 && <span><b>{lote.totais.linhas}</b> estradas</span>}
            <span className={lote.totais.nErros ? 'tem-erro' : ''}><b>{lote.totais.nErros.toLocaleString('pt-BR')}</b> ocorrências em aberto</span>
            <span className="sub">versão {lote.versaoAtual}</span>
          </p>
        </div>
        <Etapas atual={1} loteId={id} />
        <div className="barra-acoes">
          <Menu rotulo="Baixar" itens={[
            { titulo: `Versão atual (v${lote.versaoAtual})` },
            { texto: 'Shapefile (.zip)', acao: () => baixar(`/api/lotes/${id}/exportar`, { formato: 'shp' }) },
            { texto: 'GeoJSON', acao: () => baixar(`/api/lotes/${id}/exportar`, { formato: 'geojson' }) },
            aud && { titulo: 'Relatório da auditoria' },
            aud && { texto: 'PDF', acao: () => baixar(`/api/execucoes/${aud.id}/relatorio/pdf`) },
            aud && { texto: 'Planilha (CSV)', acao: () => baixar(`/api/execucoes/${aud.id}/relatorio/csv`) },
          ]} />
          <Menu rotulo="Lote" itens={[
            { texto: 'Colunas de fazenda / talhão', dica: 'quais colunas identificam cada nível', acao: () => { setCampos(Object.fromEntries(NIVEIS.map(([k]) => [k, lote.campos[k] || '']))); setModal('campos') } },
            { texto: 'Adicionar shapefile ao lote', dica: 'mesclar outra camada', acao: () => setModal('mesclar') },
            { texto: 'Versões', dica: `${lote.versoes.length} versão(ões)`, acao: () => setModal('versoes') },
            { texto: ocupado === 'auditando' ? 'Auditando…' : 'Auditar de novo', dica: 'depois de mudar regras', acao: () => acao('auditando', async () => setLote(await api.post(`/api/lotes/${id}/auditar`, {}))) },
          ]} />
        </div>
      </div>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>
      {semCampos && (
        <div className="aviso-faixa alerta">
          Não identifiquei a coluna de {!lote.campos.fazenda ? 'fazenda' : 'talhão'} nesta base.{' '}
          <button className="btn-link" onClick={() => { setCampos(Object.fromEntries(NIVEIS.map(([k]) => [k, lote.campos[k] || '']))); setModal('campos') }}>Escolher as colunas</button>
        </div>
      )}
      {avisos.length > 0 && (
        <details className="aviso-faixa alerta"><summary>{avisos.length} aviso(s) na leitura dos arquivos</summary>
          <ul>{avisos.map((a) => <li key={a}>{a}</li>)}</ul></details>
      )}
      <Dica id="lote">
        <b>Como usar:</b> 1) escolha uma fazenda à esquerda · 2) clique numa ocorrência para vê-la no mapa · 3) use
        <b> Revisar</b> para comparar o original com a correção e aprovar. Nada é alterado até a etapa 3.
      </Dica>

      <div className="tres-colunas">
        <aside className="painel">
          <div className="painel-cabeca"><h3>Fazendas</h3><small className="sub">por aglomerado</small></div>
          <ListaFazendas fazendas={lote.fazendas} atual={faz} aoEscolher={escolher} total={lote.totais.nErros} />
        </aside>

        <div className="centro">
          <Mapa camadas={camadas} enquadrar={enquadrar} chaveEnquadrar={`${faz}|${sel?.id}|${!!mapa}`}
            rotulo={titulo}
            legenda={[{ texto: 'Sem ocorrência', cor: ESTILOS.talhao.color }, { texto: 'Com ocorrência', cor: ESTILOS.comErro.color },
              ...(lote.totais.linhas ? [{ texto: 'Estrada', cor: ESTILOS.estrada.color }] : []),
              { texto: 'Problema grave', cor: ESTILOS.gravidade.alta.color, fundo: ESTILOS.gravidade.alta.fillColor },
              { texto: 'Problema médio', cor: ESTILOS.gravidade.media.color, fundo: ESTILOS.gravidade.media.fillColor },
              { texto: 'Problema leve', cor: ESTILOS.gravidade.baixa.color, fundo: ESTILOS.gravidade.baixa.fillColor }]}>
            {ficha && (
              <div className="ficha">
                <div className="titulo-secao">
                  <h3>{ficha.linha ? `Estrada (registro ${ficha.registro})` : `Talhão ${ficha.talhao || '?'}`}</h3>
                  <button className="btn-link" onClick={() => setFicha(null)}>fechar</button>
                </div>
                <dl className="dl">
                  <dt>Fazenda</dt><dd>{ficha.fazenda || '—'}{ficha.fazendaNome ? ` · ${ficha.fazendaNome}` : ''}</dd>
                  <dt>Aglomerado</dt><dd>{ficha.aglomerado || '—'}</dd>
                  <dt>{ficha.linha ? 'Comprimento' : 'Área'}</dt><dd>{ficha.linha ? `${num(ficha.comprimentoM, 0)} m` : `${num(ficha.areaHa, 2)} ha`}</dd>
                  <dt>Vértices / partes</dt><dd>{ficha.vertices} / {ficha.partes}</dd>
                </dl>
                {ficha.ocorrencias.length > 0 && (
                  <ul className="ficha-ocorrencias">
                    {ficha.ocorrencias.map((o) => (
                      <li key={o.id}><button className="btn-link" onClick={() => { const i = incs.find((x) => x.id === o.id); if (i) { setSel(i); setFicha(null) } }}>#{o.id} {o.nome}</button> <Estado s={o.status} /></li>
                    ))}
                  </ul>
                )}
                <details><summary>Atributos ({Object.keys(ficha.atributos).length})</summary>
                  <dl className="dl atributos">{Object.entries(ficha.atributos).map(([k, v]) => <ItemDl key={k} k={k} v={v} />)}</dl>
                </details>
              </div>
            )}
          </Mapa>
          {carregando && <div className="mapa-carregando"><span className="girando" /> Carregando {titulo}…</div>}
        </div>

        <aside className="painel painel-ocorrencias">
          <div className="painel-cabeca">
            <h3>Ocorrências <span className="badge erro">{incs.length}</span></h3>
            <small className="sub">{titulo}</small>
          </div>
          {tiposComErro.length > 0 && (
            <div className="filtro-chips">
              <button className={`chip ${!tipo ? 'ativo' : ''}`} onClick={() => setTipo(null)}>Todas <b>{incs.length}</b></button>
              {tiposComErro.map((t) => (
                <button key={t.chave} className={`chip ${tipo === t.chave ? 'ativo' : ''}`} onClick={() => setTipo(tipo === t.chave ? null : t.chave)}>
                  <span className={`sev sev-${t.severidade}`} />{t.nome} <b>{contagem[t.chave]}</b>
                </button>
              ))}
            </div>
          )}
          <ul className="lista-incs rolagem">
            {filtradas.map((i) => (
              <li key={i.id} className={sel?.id === i.id ? 'ativo' : ''} onClick={() => { setFicha(null); setSel(sel?.id === i.id ? null : i) }}
                onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <div className="inc-topo"><span className={`sev sev-${i.severidade}`} /><b>{i.nome}</b>
                  {i.temCorrecao ? <span className="etiqueta auto" title="Tem correção automática">auto</span> : <span className="etiqueta" title="Exige correção manual">manual</span>}
                </div>
                <div className="inc-desc">{i.descricao}</div>
                {sel?.id === i.id && (
                  <div className="inc-acoes">
                    {i.areaM2 > 0 && <small className="sub">{area(i.areaM2)}</small>}
                    <button className="btn pequeno primario" onClick={(e) => { e.stopPropagation(); revisar({ inc: i.id }) }}>Revisar esta →</button>
                  </div>
                )}
              </li>
            ))}
            {!filtradas.length && !carregando && <li className="vazio">Nenhuma ocorrência em aberto aqui. ✓</li>}
          </ul>
          <div className="rodape-painel">
            {faz === TODAS ? (
              <p className="sub">Escolha uma fazenda à esquerda para revisar as ocorrências dela.</p>
            ) : (
              <button className="btn primario largo grande" disabled={!filtradas.length} onClick={() => revisar(tipo ? { tipo } : {})}>
                Revisar {filtradas.length} ocorrência{filtradas.length === 1 ? '' : 's'} →
              </button>
            )}
          </div>
        </aside>
      </div>

      {modal === 'campos' && campos && (
        <div className="modal-fundo" onClick={() => setModal(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Colunas da base</h2>
            <p className="sub">Escolha a coluna que identifica cada nível. Ao salvar, o lote é auditado de novo.</p>
            {NIVEIS.map(([k, rot]) => (
              <label key={k}>{rot}
                <select value={campos[k]} onChange={(e) => setCampos({ ...campos, [k]: e.target.value })}>
                  <option value="">— não usar —</option>
                  {lote.colunas.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
            ))}
            <div className="acoes">
              <button className="btn" onClick={() => setModal(null)}>Cancelar</button>
              <button className="btn primario" disabled={!!ocupado} onClick={() => acao('campos', async () => { setLote(await api.put(`/api/lotes/${id}/campos`, campos)); setModal(null) })}>
                {ocupado === 'campos' ? 'Salvando e auditando…' : 'Salvar'}
              </button>
            </div>
          </div>
        </div>
      )}
      {modal === 'mesclar' && (
        <div className="modal-fundo" onClick={() => setModal(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Adicionar shapefile ao lote</h2>
            <p className="sub">As feições entram numa versão nova do lote e são auditadas junto com as outras.</p>
            <SoltarArquivos compacto arquivos={arquivos} setArquivos={setArquivos} />
            <div className="acoes">
              <button className="btn" onClick={() => setModal(null)}>Cancelar</button>
              <button className="btn primario" disabled={!arquivos.length || !!ocupado} onClick={() => acao('mesclando', async () => {
                const fd = new FormData(); arquivos.forEach((f) => fd.append('arquivos', f))
                setLote(await api.post(`/api/lotes/${id}/camadas`, fd)); setArquivos([]); setModal(null)
              })}>{ocupado === 'mesclando' ? 'Adicionando e auditando…' : 'Adicionar'}</button>
            </div>
          </div>
        </div>
      )}
      {modal === 'versoes' && (
        <div className="modal-fundo" onClick={() => setModal(null)}>
          <div className="modal largo" onClick={(e) => e.stopPropagation()}>
            <div className="titulo-secao"><h2>Versões do lote</h2><button className="btn-link" onClick={() => setModal(null)}>fechar</button></div>
            <p className="sub">A versão 1 é a base original e nunca é alterada.</p>
            <table className="tabela compacta">
              <thead><tr><th>Versão</th><th>O que mudou</th><th>Por</th><th>Em</th><th /></tr></thead>
              <tbody>{[...lote.versoes].reverse().map((v) => (
                <tr key={v.numero}><td><b>v{v.numero}</b></td><td>{v.descricao}</td><td>{v.criadoPor}</td><td className="nowrap">{data(v.criadoEm)}</td>
                  <td className="dir"><button className="btn pequeno" onClick={() => baixar(`/api/lotes/${id}/exportar`, { formato: 'shp', versao: v.numero })}>Baixar</button></td></tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function ItemDl({ k, v }) {
  return <><dt>{k}</dt><dd>{v === null || v === '' ? '—' : String(v)}</dd></>
}
