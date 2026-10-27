// Etapa 3: relatório de preparação, confirmação, execução com progresso, validação de cada correção,
// nova versão e downloads. A base original fica preservada (a versão 1 continua disponível).
import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, data, num } from '../api.js'
import Mapa, { ESTILOS } from '../components/Mapa.jsx'
import { Aviso, Estado, Etapas } from '../components/Comuns.jsx'

export default function Correcao() {
  const { id } = useParams()
  const { state } = useLocation()
  const nav = useNavigate()
  const ids = state?.ids || []
  const fazenda = state?.fazenda ?? '*'
  const [prep, setPrep] = useState(null)
  const [exe, setExe] = useState(null)
  const [mapas, setMapas] = useState({})
  const [erro, setErro] = useState('')
  const [ocupado, setOcupado] = useState('')

  useEffect(() => {
    if (!ids.length) return
    api.post(`/api/lotes/${id}/correcao/preparar`, { ids }).then(setPrep).catch((e) => setErro(e.message))
  }, [id])

  // acompanha a execução até terminar
  useEffect(() => {
    if (!exe || exe.status !== 'executando') return
    const t = setTimeout(() => api.get(`/api/execucoes/${exe.id}`).then(setExe).catch((e) => setErro(e.message)), 500)
    return () => clearTimeout(t)
  }, [exe])

  useEffect(() => {
    if (exe?.status !== 'concluida' || !exe.resumo.novaVersao) return
    const p = exe.fazendas.length === 1 ? { fazenda: exe.fazendas[0] } : {}
    Promise.all([api.get(`/api/lotes/${id}/mapa`, { ...p, versao: exe.versaoSaida }), api.get(`/api/lotes/${id}/mapa`, { ...p, versao: exe.versaoEntrada })])
      .then(([depois, antes]) => setMapas({ depois, antes })).catch(() => {})
  }, [exe?.status])

  const executar = async () => {
    setErro(''); setOcupado('executando')
    try {
      const { execucaoId } = await api.post(`/api/lotes/${id}/correcao`, { ids })
      setExe(await api.get(`/api/execucoes/${execucaoId}`))
    } catch (e) { setErro(e.message) } finally { setOcupado('') }
  }
  const baixar = async (url, params, qual) => {
    setOcupado(qual)
    try { await api.baixar(url, params) } catch (e) { setErro(e.message) } finally { setOcupado('') }
  }

  const r = exe?.resumo
  const alteradas = new Set(r?.alteradas?.map((a) => a.id) || [])
  const camadas = useMemo(() => !mapas.depois ? [] : [
    { dados: mapas.depois, estilo: (f) => alteradas.has(f.properties.id) ? (f.properties.linha ? { ...ESTILOS.estrada, color: '#3ddc84' } : ESTILOS.alterado)
      : f.properties.linha ? ESTILOS.estrada : ESTILOS.talhao,
      dica: (f) => `${f.properties.linha ? `Linha #${f.properties.indice + 1}` : `Talhão ${f.properties.talhao || '?'}`}${alteradas.has(f.properties.id) ? ' · alterado' : ''}` },
    { dados: { ...mapas.antes, features: mapas.antes.features.filter((f) => alteradas.has(f.properties.id)) },
      estilo: { color: '#ff1f3d', weight: 2, dashArray: '5 5', fillOpacity: 0 } },
  ], [mapas])
  const voltar = `/lotes/${id}?fazenda=${encodeURIComponent(fazenda)}`

  if (!ids.length) {
    return (
      <div className="pagina"><div className="cartao">
        <p>Nenhuma revisão em andamento. Comece pela auditoria do lote.</p>
        <Link className="btn" to={`/lotes/${id}`}>Ir para a auditoria</Link>
      </div></div>
    )
  }

  const Lista = ({ titulo, itens, vazio }) => (
    <details className="lista-prep" open={itens.length > 0 && itens.length <= 8}>
      <summary><b>{titulo}</b> ({itens.length})</summary>
      {itens.length ? <ul>{itens.map((i) => <li key={i.id}>#{i.id} {i.nome} — {i.descricao} <Estado s={i.status} /></li>)}</ul> : <p className="sub">{vazio}</p>}
    </details>
  )

  return (
    <div className="tela-lote">
      <div className="barra-lote">
        <div>
          {!exe && <Link to={`/lotes/${id}/revisao?fazenda=${encodeURIComponent(fazenda)}`} className="btn-link">← Voltar à revisão</Link>}
          <h1>Correção automática {fazenda === '*' ? '' : `— fazenda ${fazenda || 'sem código / estradas'}`}</h1>
        </div>
        <Etapas atual={3} loteId={id} fazenda={fazenda} />
      </div>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>

      {!exe && (
        <div className="pagina">
          <div className="cartao">
            <h2>Preparação da execução</h2>
            {!prep ? <p className="sub">Carregando…</p> : (
              <>
                <div className="numeros grande">
                  <div><b className="verde">{prep.aprovadas.length}</b><span>correções aprovadas</span></div>
                  <div><b>{prep.naoCorrigiveis.length}</b><span>não corrigíveis automaticamente</span></div>
                  <div><b>{prep.rejeitadas.length}</b><span>rejeitadas</span></div>
                  <div><b>{prep.pendentes.length}</b><span>pendentes</span></div>
                </div>
                <h3>Operações que serão executadas</h3>
                <table className="tabela compacta">
                  <thead><tr><th>Operação</th><th>Quantidade</th></tr></thead>
                  <tbody>{prep.operacoes.map((o) => <tr key={o.tipo}><td>{o.nome}</td><td>{o.quantidade}</td></tr>)}</tbody>
                </table>
                <h3>Saída</h3>
                <p>{prep.saida}</p>
                <h3>Avisos e limitações</h3>
                <ul className="avisos">{prep.avisos.map((a) => <li key={a}>{a}</li>)}</ul>
                <Lista titulo="Aprovadas (serão executadas)" itens={prep.aprovadas} vazio="Nenhuma." />
                <Lista titulo="Não poderão ser corrigidas automaticamente" itens={prep.naoCorrigiveis} vazio="Nenhuma." />
                <Lista titulo="Rejeitadas" itens={prep.rejeitadas} vazio="Nenhuma." />
                <Lista titulo="Pendentes (sem decisão)" itens={prep.pendentes} vazio="Nenhuma." />
                <div className="acoes">
                  <button className="btn primario grande" disabled={!!ocupado || !prep.aprovadas.length} onClick={executar}>
                    {ocupado === 'executando' ? 'Iniciando…' : `Confirmar e executar ${prep.aprovadas.length} correção(ões)`}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {exe?.status === 'executando' && (
        <div className="pagina"><div className="cartao">
          <h2>Executando… <small className="sub">execução #{exe.id}</small></h2>
          <div className="progresso grande"><div style={{ width: `${exe.progresso.total ? (exe.progresso.feito / exe.progresso.total) * 100 : 5}%` }} /></div>
          <p className="sub">{exe.progresso.feito} de {exe.progresso.total} · {exe.progresso.etapa}</p>
        </div></div>
      )}

      {exe?.status === 'erro' && (
        <div className="pagina"><div className="cartao">
          <h2>A execução #{exe.id} foi interrompida</h2>
          <p className="erro">{exe.erro}</p>
          <p>Nenhuma alteração foi gravada. As ocorrências continuam aprovadas; você pode tentar de novo.</p>
          <Link className="btn" to={voltar}>Voltar à auditoria</Link>
        </div></div>
      )}

      {exe?.status === 'concluida' && (
        <div className="resultado">
          <div className="resultado-info">
            <div className="cartao">
              <h2>{r.novaVersao ? `✓ Versão ${exe.versaoSaida} gerada` : 'Execução concluída sem alterar a base'} <small className="sub">execução #{exe.id} · {data(exe.fim)}</small></h2>
              <div className="numeros">
                <div><b className="verde">{r.corrigidas}</b><span>corrigidas e validadas</span></div>
                <div><b className={r.falhas ? 'vermelho' : ''}>{r.falhas}</b><span>falhas na correção</span></div>
                <div><b>{r.pendentes}</b><span>pendentes</span></div>
                <div><b className={r.semSolucao ? 'laranja' : ''}>{r.semSolucao}</b><span>sem solução</span></div>
                <div><b className={r.novosProblemas ? 'laranja' : ''}>{r.novosProblemas}</b><span>novos na reauditoria</span></div>
              </div>
              <p className="sub">Saída: {exe.saida}</p>
              {r.falhas > 0 && <p className="aviso">As correções reprovadas na validação foram descartadas e ficam como "Falha na correção" para nova revisão.</p>}
              {r.novosProblemas > 0 && <p className="aviso">A reauditoria das fazendas corrigidas encontrou {r.novosProblemas} ocorrência(s) nova(s) (ex.: um reparo que dividiu o talhão em partes).</p>}
            </div>

            <div className="cartao">
              <h3>Resultado da validação</h3>
              <table className="tabela compacta">
                <thead><tr><th>#</th><th>Regra / operação</th><th>Resultado</th><th>Verificações</th></tr></thead>
                <tbody>
                  {r.resultados.map((x) => (
                    <tr key={x.id}>
                      <td>{x.id}</td>
                      <td>{x.nome}<br /><small className="sub">{x.operacao}</small></td>
                      <td><Estado s={x.status} /></td>
                      <td><ul className="validacao">{x.validacao.map((v) => <li key={v.nome} className={v.ok ? 'ok' : 'nok'}>{v.ok ? '✓' : '✗'} {v.nome}: {v.detalhe}</li>)}</ul></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {r.alteradas?.length > 0 && (
              <div className="cartao">
                <h3>Feições alteradas na versão {exe.versaoSaida}</h3>
                <table className="tabela compacta">
                  <thead><tr><th>Feição</th><th>Ajustes</th><th>Antes</th><th>Depois</th><th>Vértices</th></tr></thead>
                  <tbody>
                    {r.alteradas.map((a) => (
                      <tr key={a.id}><td>{a.linha ? 'Linha' : `Talhão ${a.talhao || '?'}`} <small>({a.fazenda || '—'})</small></td><td>{a.ajustes.join(', ')}</td>
                        <td>{a.linha ? '—' : `${num(a.areaAntes, 4)} ha`}</td><td>{a.removida ? 'removido' : a.linha ? '—' : `${num(a.areaDepois, 4)} ha`}</td>
                        <td>{a.verticesAntes} → {a.verticesDepois}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="cartao acoes-finais">
              <h3>Downloads</h3>
              <div className="botoes">
                {r.novaVersao && <>
                  <button className="btn primario" disabled={!!ocupado} onClick={() => baixar(`/api/lotes/${id}/exportar`, { formato: 'shp', versao: exe.versaoSaida }, 'shp')}>
                    Nova base: {r.arquivo}
                  </button>
                  <button className="btn" disabled={!!ocupado} onClick={() => baixar(`/api/lotes/${id}/exportar`, { formato: 'geojson', versao: exe.versaoSaida }, 'geojson')}>GeoJSON</button>
                </>}
                <button className="btn" disabled={!!ocupado} onClick={() => baixar(`/api/execucoes/${exe.id}/relatorio/pdf`, null, 'pdf')}>Relatório da execução (PDF)</button>
                <button className="btn" disabled={!!ocupado} onClick={() => baixar(`/api/execucoes/${exe.id}/relatorio/csv`, null, 'csv')}>Relatório (CSV)</button>
              </div>
              <div className="opcoes-finais">
                <button className="btn primario grande" onClick={() => nav(voltar)}>Voltar ao mapa principal</button>
                <button className="btn grande" disabled title="O plugin do QGIS entra na próxima fase do projeto">
                  Enviar ao QGIS <small>(plugin em breve)</small>
                </button>
              </div>
            </div>
          </div>
          <div className="centro">
            {r.novaVersao ? (
              <Mapa camadas={camadas} enquadrar={mapas.antes ? { ...mapas.antes, features: mapas.antes.features.filter((f) => alteradas.has(f.properties.id)) } : null}
                chaveEnquadrar={!!mapas.antes} rotulo={`Versão ${exe.versaoSaida}`}
                legenda={[{ texto: 'Alterado (novo)', cor: ESTILOS.alterado.color, fundo: ESTILOS.alterado.fillColor }, { texto: 'Como era', cor: '#ff1f3d', tipo: 'tracejado' },
                  { texto: 'Sem alteração', cor: ESTILOS.talhao.color }]} />
            ) : <div className="cartao"><p>Nenhuma geometria mudou nesta execução.</p></div>}
          </div>
        </div>
      )}
    </div>
  )
}
