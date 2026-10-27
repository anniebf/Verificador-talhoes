import { useEffect, useState } from 'react'
import { api, data } from '../api.js'
import { Aviso, SEVERIDADE } from '../components/Comuns.jsx'

// rótulos dos parâmetros (o backend manda só a chave e o valor)
const ROTULOS = {
  tolerancia_m: 'Tolerância (m)',
  area_max_ha: 'Área máxima (ha)',
  ignorar_estradas: 'Ignorar faixas com formato de estrada',
  max_vertices: 'Máximo de vértices',
  espacamento_min_m: 'Espaçamento mínimo entre vértices (m)',
  variacao_area_max_pct: 'Variação de área aceitável (%)',
  area_min_m2: 'Área mínima (m²)',
  area_max_m2: 'Área máxima (m²)',
  recortar_de: 'Recortar a sobreposição de qual talhão',
  razao_max_pct: 'Proporção máxima da parte no talhão (%)',
  area_max_preencher_ha: 'Preencher automaticamente até (ha)',
  largura_min_m: 'Largura mínima de estrada (m)',
  largura_max_m: 'Largura máxima de estrada (m)',
  comprimento_min_m: 'Comprimento mínimo de estrada (m)',
  cruzamento_min_m: 'Trecho mínimo de estrada dentro do talhão (m)',
  largura_faixa_m: 'Largura da faixa recortada (m)',
  propor_recorte: 'Propor recorte da faixa quando houver camada de estradas',
  distancia_max_m: 'Distância máxima para considerar desconexão (m)',
}
const CAMPOS = {
  fazenda: 'Código da fazenda', fazenda_nome: 'Nome da fazenda', fazenda_detalhe: 'Detalhe do nome (junto quando diferente: "MALU · EMA")', aglomerado: 'Aglomerado', talhao: 'Talhão', area_ha: 'Área (ha), recalculada na exportação',
}

export default function Regras() {
  const [dados, setDados] = useState(null)
  const [edit, setEdit] = useState({})
  const [erro, setErro] = useState('')
  const [ok, setOk] = useState('')

  const receber = (d) => {
    setDados(d)
    setEdit({ ...Object.fromEntries(d.regras.map((r) => [r.chave, { ativo: r.ativo, params: { ...r.params } }])),
      campos: { ativo: true, params: Object.fromEntries(Object.entries(d.campos).map(([k, v]) => [k, v.join(', ')])) } })
  }
  useEffect(() => { api.get('/api/admin/regras').then(receber).catch((e) => setErro(e.message)) }, [])

  const salvar = async (chave) => {
    setErro('')
    try {
      const e = edit[chave]
      const params = chave === 'campos'
        ? Object.fromEntries(Object.entries(e.params).map(([k, v]) => [k, v.split(',').map((x) => x.trim()).filter(Boolean)]))
        : e.params
      receber(await api.put(`/api/admin/regras/${chave}`, chave === 'campos' ? { params } : { ativo: e.ativo, params }))
      setOk('Regra salva. Ela vale para as próximas auditorias (use "Reauditar" no lote para aplicar agora).')
    } catch (e) { setErro(e.message) }
  }
  const restaurar = async (chave) => {
    if (!confirm('Voltar esta regra aos valores padrão?')) return
    try { receber(await api.post(`/api/admin/regras/${chave}/padrao`)) } catch (e) { setErro(e.message) }
  }
  const mudar = (chave, k, v) => setEdit({ ...edit, [chave]: { ...edit[chave], params: { ...edit[chave].params, [k]: v } } })

  if (!dados) return <div className="pagina"><Aviso>{erro}</Aviso></div>

  return (
    <div className="pagina">
      <h1>Regras de detecção e correção</h1>
      <p className="sub">Parâmetros usados na auditoria (etapa 1) e nas correções propostas (etapa 2). Toda mudança fica no log de auditoria.</p>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>
      <Aviso tipo="ok" aoFechar={() => setOk('')}>{ok}</Aviso>

      <div className="grade-regras">
        {dados.regras.map((r) => {
          const e = edit[r.chave]
          return (
            <div key={r.chave} className={`cartao regra ${e.ativo ? '' : 'desligada'}`}>
              <div className="titulo-secao">
                <h3><span className={`sev sev-${r.severidade}`} title={`Severidade ${SEVERIDADE[r.severidade]}`} /> <small className="grupo">{r.grupo}</small> {r.nome}</h3>
                <label className="chave">
                  <input type="checkbox" checked={e.ativo} onChange={(x) => setEdit({ ...edit, [r.chave]: { ...e, ativo: x.target.checked } })} />
                  {e.ativo ? 'Ativa' : 'Desligada'}
                </label>
              </div>
              <p className="sub">{r.descricao}</p>
              {r.limitacoes && <p className="sub"><b>Riscos:</b> {r.limitacoes}</p>}
              {Object.entries(e.params).map(([k, v]) => (
                <label key={k} className={typeof v === 'boolean' ? 'linha-check' : ''}>
                  {typeof v === 'boolean' ? (
                    <><input type="checkbox" checked={v} onChange={(x) => mudar(r.chave, k, x.target.checked)} /> {ROTULOS[k] || k}</>
                  ) : k === 'recortar_de' ? (
                    <>{ROTULOS[k]}
                      <select value={v} onChange={(x) => mudar(r.chave, k, x.target.value)}>
                        <option value="maior">Do maior (muda menos em proporção)</option>
                        <option value="menor">Do menor</option>
                      </select></>
                  ) : (
                    <>{ROTULOS[k] || k} <small className="sub">(padrão {r.padrao[k]})</small>
                      <input type="number" step="any" min="0" value={v} onChange={(x) => mudar(r.chave, k, x.target.value)} /></>
                  )}
                </label>
              ))}
              <div className="acoes">
                {r.atualizadoPor && <small className="sub cresce">Alterada por {r.atualizadoPor} em {data(r.atualizadoEm)}</small>}
                <button className="btn pequeno" onClick={() => restaurar(r.chave)}>Padrão</button>
                <button className="btn pequeno primario" onClick={() => salvar(r.chave)}>Salvar</button>
              </div>
            </div>
          )
        })}

        <div className="cartao regra largo">
          <h3>Mapeamento de campos (hierarquia)</h3>
          <p className="sub">Nomes de coluna aceitos para cada nível, em ordem de preferência, separados por vírgula. Vale para as próximas importações.</p>
          {Object.entries(CAMPOS).map(([k, rot]) => (
            <label key={k}>{rot}
              <input value={edit.campos.params[k] || ''} onChange={(x) => mudar('campos', k, x.target.value)} />
            </label>
          ))}
          <div className="acoes">
            <button className="btn pequeno" onClick={() => restaurar('campos')}>Padrão</button>
            <button className="btn pequeno primario" onClick={() => salvar('campos')}>Salvar</button>
          </div>
        </div>
      </div>
    </div>
  )
}
