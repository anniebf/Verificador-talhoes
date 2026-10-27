// Etapa 2: revisão visual lado a lado (como um "diff" do git): original com erro x proposta corrigida.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, area, num } from '../api.js'
import Mapa, { ESTILOS, sincronizar } from '../components/Mapa.jsx'
import { Aviso, Etapas, SEVERIDADE, STATUS } from '../components/Comuns.jsx'

const FC = (features) => ({ type: 'FeatureCollection', features })
const decidida = (i) => i.status === 'aceita' || i.status === 'rejeitada'

export default function Revisao() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const nav = useNavigate()
  const fazenda = sp.get('fazenda') ?? '*'
  const unico = sp.get('inc') ? Number(sp.get('inc')) : null
  const tipo = sp.get('tipo') || undefined
  const [todas, setTodas] = useState(null)
  const [atualId, setAtualId] = useState(null)
  const [det, setDet] = useState(null)
  const [erro, setErro] = useState('')
  const [salvando, setSalvando] = useState(false)
  const mapas = useRef([])

  useEffect(() => {
    api.get(`/api/lotes/${id}/inconsistencias`, { fazenda: fazenda === '*' ? undefined : fazenda, tipo })
      .then((l) => setTodas(l)).catch((e) => setErro(e.message))
  }, [id, fazenda, tipo])

  const lista = useMemo(() => (todas ? (unico ? todas.filter((i) => i.id === unico) : todas) : []), [todas, unico])

  useEffect(() => {
    if (!lista.length) return
    if (!lista.some((i) => i.id === atualId)) setAtualId((lista.find((i) => !decidida(i)) || lista[0]).id)
  }, [lista])

  useEffect(() => {
    if (!atualId) return
    setDet(null)
    api.get(`/api/lotes/${id}/inconsistencias/${atualId}`).then(setDet).catch((e) => setErro(e.message))
  }, [id, atualId])

  const aoCriarMapa = useCallback((m) => {
    mapas.current.push(m)
    if (mapas.current.length === 2) sincronizar(mapas.current[0], mapas.current[1])
    return () => { mapas.current = mapas.current.filter((x) => x !== m) }
  }, [])

  const atual = lista.find((i) => i.id === atualId)
  const pos = lista.findIndex((i) => i.id === atualId)
  const feitas = lista.filter(decidida).length

  const decidir = async (ids, decisao, avancar = true) => {
    setSalvando(true); setErro('')
    try {
      const r = await api.post(`/api/lotes/${id}/decisoes`, { ids, decisao })
      const por = Object.fromEntries(r.map((x) => [x.id, x]))
      const nova = todas.map((i) => (por[i.id] ? { ...i, ...por[i.id] } : i))
      setTodas(nova)
      if (avancar && ids.length === 1) {
        const visiveis = unico ? nova.filter((i) => i.id === unico) : nova
        const k = visiveis.findIndex((i) => i.id === ids[0])
        const prox = [...visiveis.slice(k + 1), ...visiveis.slice(0, k)].find((i) => !decidida(i))
        if (prox) setAtualId(prox.id)
      }
    } catch (e) { setErro(e.message) } finally { setSalvando(false) }
  }

  const emLote = (decisao) => {
    const alvo = lista.filter((i) => !decidida(i) && (decisao === 'manter' || i.temCorrecao))
    if (!alvo.length) return
    const txt = decisao === 'aceitar'
      ? `Aceitar a correção automática nas ${alvo.length} inconsistência(s) ainda não decididas que têm correção?`
      : `Manter o original nas ${alvo.length} inconsistência(s) ainda não decididas?`
    if (confirm(txt)) decidir(alvo.map((i) => i.id), decisao, false)
  }

  // atalhos: A aceita, M mantém, setas navegam
  useEffect(() => {
    const tecla = (e) => {
      if (!atual || salvando || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return
      if (e.key === 'a' && atual.temCorrecao) decidir([atual.id], 'aceitar')
      else if (e.key === 'm') decidir([atual.id], 'manter')
      else if (e.key === 'ArrowRight' && pos < lista.length - 1) setAtualId(lista[pos + 1].id)
      else if (e.key === 'ArrowLeft' && pos > 0) setAtualId(lista[pos - 1].id)
    }
    window.addEventListener('keydown', tecla)
    return () => window.removeEventListener('keydown', tecla)
  })

  const destaque = det?.destaque ? FC([{ type: 'Feature', geometry: det.destaque, properties: {} }]) : null
  const viz = det && {
    dados: det.vizinhos, estilo: ESTILOS.vizinho,
    dica: (f) => `Talhão ${f.properties.talhao || '?'} (vizinho)`,
  }
  const dicaT = (f) => `Talhão ${f.properties.talhao || '?'}${f.properties.aglomerado ? ` · ${f.properties.aglomerado}` : ''}`
  const esquerda = useMemo(() => det ? [viz, { dados: det.original, estilo: ESTILOS.original, dica: dicaT },
    destaque && { dados: destaque, estilo: ESTILOS.erro, ponto: { fillColor: '#ff1f3d' }, dica: () => det.nome }].filter(Boolean) : [], [det])
  const direita = useMemo(() => det ? [viz, { dados: det.proposta, estilo: ESTILOS.proposta, dica: dicaT },
    destaque && { dados: destaque, estilo: { color: '#fff', weight: 1.5, dashArray: '4 4', fillOpacity: 0 }, ponto: { fillColor: '#3ddc84', radius: 5 } }].filter(Boolean) : [], [det])
  const enquadrar = det ? [det.original, destaque].filter(Boolean) : null

  const prosseguir = () => nav(`/lotes/${id}/correcao`, { state: { ids: lista.map((i) => i.id), fazenda } })
  const voltar = `/lotes/${id}?fazenda=${encodeURIComponent(fazenda)}`

  if (todas === null) return <div className="pagina"><Aviso>{erro}</Aviso>{!erro && <p className="sub">Carregando…</p>}</div>

  return (
    <div className="tela-lote">
      <div className="barra-lote">
        <div>
          <Link to={voltar} className="btn-link">← Voltar à auditoria</Link>
          <h1>Revisão visual {fazenda === '*' ? '— todas as fazendas' : `— fazenda ${fazenda || 'sem código'}`}</h1>
        </div>
        <Etapas atual={2} loteId={id} fazenda={fazenda} />
      </div>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>

      {!lista.length ? (
        <div className="pagina"><div className="cartao"><p>Nenhuma inconsistência em aberto neste filtro.</p>
          <Link className="btn" to={voltar}>Voltar à auditoria</Link></div></div>
      ) : (
        <div className="revisao">
          <aside className="painel">
            <section>
              <h3>O que revisar</h3>
              <div className="escopo">
                <label><input type="radio" checked={!unico} onChange={() => { sp.delete('inc'); setSp(sp) }} />
                  Pacote completo {fazenda === '*' ? 'do lote' : 'da fazenda'} ({todas.length})</label>
                <label><input type="radio" checked={!!unico} onChange={() => { sp.set('inc', atualId); setSp(sp) }} /> Somente um erro</label>
                {unico && (
                  <select value={unico} onChange={(e) => { sp.set('inc', e.target.value); setSp(sp) }}>
                    {todas.map((i) => <option key={i.id} value={i.id}>#{i.id} {i.nome} — {i.feicoes.map((f) => f.talhao || '?').join(', ')}</option>)}
                  </select>
                )}
              </div>
              <div className="progresso"><div style={{ width: `${(feitas / lista.length) * 100}%` }} /></div>
              <p className="sub">{feitas} de {lista.length} decididas</p>
              {lista.length > 1 && (
                <div className="botoes">
                  <button className="btn pequeno" disabled={salvando} onClick={() => emLote('aceitar')}>Aceitar todas as correções</button>
                  <button className="btn pequeno" disabled={salvando} onClick={() => emLote('manter')}>Manter todas</button>
                </div>
              )}
            </section>
            <section className="cresce">
              <ul className="lista-incs">
                {lista.map((i, k) => (
                  <li key={i.id} className={i.id === atualId ? 'ativo' : ''} onClick={() => setAtualId(i.id)}>
                    <div className="inc-topo">
                      <span className={`sev sev-${i.severidade}`} /><b>{k + 1}. {i.nome}</b>
                      <span className={`etiqueta ${STATUS[i.status].cls}`}>{STATUS[i.status].texto}</span>
                    </div>
                    <div className="inc-desc">{i.descricao}</div>
                  </li>
                ))}
              </ul>
            </section>
            <div className="rodape-painel">
              <button className="btn primario largo" disabled={feitas < lista.length} onClick={prosseguir}
                title={feitas < lista.length ? 'Decida todas as inconsistências antes de prosseguir' : ''}>
                Prosseguir para a correção →
              </button>
              {feitas < lista.length && <small className="sub">Falta decidir {lista.length - feitas}.</small>}
            </div>
          </aside>

          <div className="comparacao">
            <div className="dois-mapas">
              <Mapa aoCriar={aoCriarMapa} camadas={esquerda} enquadrar={enquadrar} chaveEnquadrar={det?.id}
                rotulo="Atual — com erro" className="lado-original"
                legenda={[{ texto: 'Talhão envolvido', cor: ESTILOS.original.color }, { texto: 'Trecho com erro', cor: ESTILOS.erro.color, fundo: ESTILOS.erro.fillColor }, { texto: 'Vizinhos', cor: '#94a3b8' }]} />
              <Mapa aoCriar={aoCriarMapa} camadas={direita} rotulo={det && !det.temCorrecao ? 'Sem correção automática' : 'Proposta — corrigido'}
                className="lado-proposta"
                legenda={[{ texto: 'Resultado da correção', cor: ESTILOS.proposta.color }, { texto: 'Onde estava o erro', cor: '#fff' }]} />
              {!det && <div className="mapa-carregando">Carregando…</div>}
            </div>

            {atual && (
              <div className="decisao">
                <div className="decisao-info">
                  <div className="inc-topo">
                    <span className={`sev sev-${atual.severidade}`} /><b>#{atual.id} · {atual.nome}</b>
                    <span className="sub">Severidade {SEVERIDADE[atual.severidade]} · {pos + 1} de {lista.length}</span>
                  </div>
                  <p>{atual.descricao}</p>
                  {det && (
                    <table className="tabela compacta">
                      <thead><tr><th>Talhão</th><th>Área antes</th><th>Área depois</th><th>Vértices</th><th>Partes</th><th>Válida</th></tr></thead>
                      <tbody>
                        {det.envolvidas.map((e) => (
                          <tr key={e.id} className={e.alvo ? 'alvo' : ''}>
                            <td>{e.talhao || '?'}{e.aglomerado ? ` (${e.aglomerado})` : ''}{e.alvo ? ' ✎' : ''}</td>
                            <td>{num(e.antes.areaHa, 4)} ha</td>
                            <td>{e.removida ? 'removido' : `${num(e.depois.areaHa, 4)} ha`}
                              {!e.removida && e.depois.areaHa !== e.antes.areaHa && <small> ({e.depois.areaHa > e.antes.areaHa ? '+' : ''}{area(Math.abs(e.depois.areaHa - e.antes.areaHa) * 1e4)})</small>}</td>
                            <td>{e.antes.vertices} → {e.depois.vertices}</td>
                            <td>{e.antes.partes} → {e.depois.partes}</td>
                            <td>{e.antes.valida ? 'sim' : 'não'} → {e.depois.valida ? 'sim' : 'não'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {det?.operacao && <p className="sub">Correção proposta: <b>{det.operacao}</b> (✎ = talhão alterado)</p>}
                  {det && !det.temCorrecao && <p className="aviso">Este tipo de erro não tem correção automática. Mantenha o original e corrija no QGIS.</p>}
                </div>
                <div className="decisao-botoes">
                  <div className="nav-setas">
                    <button className="btn pequeno" disabled={pos <= 0} onClick={() => setAtualId(lista[pos - 1].id)}>← Anterior</button>
                    <button className="btn pequeno" disabled={pos >= lista.length - 1} onClick={() => setAtualId(lista[pos + 1].id)}>Próxima →</button>
                  </div>
                  <button className={`btn grande ${atual.status === 'rejeitada' ? 'escolhido-manter' : ''}`} disabled={salvando}
                    onClick={() => decidir([atual.id], 'manter')}>
                    ⟵ Manter original <kbd>M</kbd>
                  </button>
                  <button className={`btn grande primario ${atual.status === 'aceita' ? 'escolhido-aceitar' : ''}`} disabled={salvando || !atual.temCorrecao}
                    onClick={() => decidir([atual.id], 'aceitar')}>
                    Aceitar correção ⟶ <kbd>A</kbd>
                  </button>
                  <small className="sub">Decisão atual: <b>{STATUS[atual.status].texto}</b></small>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
