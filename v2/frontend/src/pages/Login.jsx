import { useState } from 'react'
import { api } from '../api.js'

export default function Login({ aoEntrar }) {
  const [usuario, setUsuario] = useState('')
  const [senha, setSenha] = useState('')
  const [erro, setErro] = useState('')
  const [enviando, setEnviando] = useState(false)

  const enviar = async (e) => {
    e.preventDefault()
    setErro('')
    setEnviando(true)
    try {
      const r = await api.post('/api/auth/login', { usuario, senha })
      aoEntrar(r.token, r.usuario)
    } catch (e) {
      setErro(e.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="login-fundo">
      <form className="login" onSubmit={enviar}>
        <div className="marca grande"><span className="marca-icone">▦</span> Verificador de Talhões <span className="versao">v2</span></div>
        <p className="sub">Auditoria, revisão e correção de shapefiles de fazendas, aglomerados e talhões.</p>
        <label>Usuário<input value={usuario} onChange={(e) => setUsuario(e.target.value)} autoFocus required autoComplete="username" /></label>
        <label>Senha<input type="password" value={senha} onChange={(e) => setSenha(e.target.value)} required autoComplete="current-password" /></label>
        {erro && <p className="erro">{erro}</p>}
        <button className="btn primario largo" disabled={enviando}>{enviando ? 'Entrando…' : 'Entrar'}</button>
        <p className="rodape-login">Login corporativo (Active Directory) em breve.</p>
      </form>
    </div>
  )
}
