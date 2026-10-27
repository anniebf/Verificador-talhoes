import { useState } from 'react'
import { api } from '../api.js'

export default function TrocarSenha({ obrigatorio, aoFechar, aoTrocar }) {
  const [atual, setAtual] = useState('')
  const [nova, setNova] = useState('')
  const [conf, setConf] = useState('')
  const [erro, setErro] = useState('')
  const [enviando, setEnviando] = useState(false)

  const enviar = async (e) => {
    e.preventDefault()
    if (nova.length < 6) return setErro('A nova senha precisa ter pelo menos 6 caracteres.')
    if (nova !== conf) return setErro('A confirmação não bate com a nova senha.')
    setEnviando(true)
    try {
      aoTrocar(await api.post('/api/auth/senha', { atual, nova }))
    } catch (e) {
      setErro(e.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="modal-fundo">
      <form className="modal" onSubmit={enviar}>
        <h2>Trocar senha</h2>
        {obrigatorio && <p className="aviso">Este é o primeiro acesso com a senha padrão. Defina uma senha nova para continuar.</p>}
        <label>Senha atual<input type="password" value={atual} onChange={(e) => setAtual(e.target.value)} autoFocus required /></label>
        <label>Nova senha<input type="password" value={nova} onChange={(e) => setNova(e.target.value)} required /></label>
        <label>Confirmar nova senha<input type="password" value={conf} onChange={(e) => setConf(e.target.value)} required /></label>
        {erro && <p className="erro">{erro}</p>}
        <div className="acoes">
          {!obrigatorio && <button type="button" className="btn" onClick={aoFechar}>Cancelar</button>}
          <button className="btn primario" disabled={enviando}>{enviando ? 'Salvando…' : 'Salvar'}</button>
        </div>
      </form>
    </div>
  )
}
