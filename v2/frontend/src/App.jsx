import { createContext, useContext, useEffect, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api, quandoExpirar, token } from './api.js'
import Login from './pages/Login.jsx'
import Lotes from './pages/Lotes.jsx'
import Auditoria from './pages/Auditoria.jsx'
import Revisao from './pages/Revisao.jsx'
import Correcao from './pages/Correcao.jsx'
import LogAuditoria from './pages/LogAuditoria.jsx'
import Usuarios from './pages/Usuarios.jsx'
import Regras from './pages/Regras.jsx'
import TrocarSenha from './components/TrocarSenha.jsx'

const Ctx = createContext(null)
export const useSessao = () => useContext(Ctx)

export default function App() {
  const [usuario, setUsuario] = useState(undefined) // undefined = ainda conferindo o token
  const [trocar, setTrocar] = useState(false)
  const nav = useNavigate()

  const sair = () => { token.set(null); setUsuario(null); nav('/login') }

  useEffect(() => {
    quandoExpirar(() => { token.set(null); setUsuario(null) })
    if (!token.get()) { setUsuario(null); return }
    api.get('/api/auth/eu').then(setUsuario).catch(() => { token.set(null); setUsuario(null) })
  }, [])

  const entrar = (t, u) => { token.set(t); setUsuario(u); nav('/') }

  if (usuario === undefined) return <div className="carregando-tela">Carregando…</div>
  if (!usuario) {
    return (
      <Routes>
        <Route path="*" element={<Login aoEntrar={entrar} />} />
      </Routes>
    )
  }
  const admin = usuario.perfil === 'admin'
  return (
    <Ctx.Provider value={{ usuario, setUsuario, admin }}>
      <div className="app">
        <header className="topo">
          <div className="marca">
            <span className="marca-icone">▦</span> Verificador de Talhões <span className="versao">v2</span>
          </div>
          <nav>
            <NavLink to="/" end>Lotes</NavLink>
            <NavLink to="/log">Log de auditoria</NavLink>
            {admin && <NavLink to="/admin/usuarios">Usuários</NavLink>}
            {admin && <NavLink to="/admin/regras">Regras de correção</NavLink>}
          </nav>
          <div className="usuario-menu">
            <span title={usuario.usuario}>{usuario.nome || usuario.usuario}</span>
            <span className={`etiqueta perfil-${usuario.perfil}`}>{usuario.perfil}</span>
            <button className="btn-link" onClick={() => setTrocar(true)}>Trocar senha</button>
            <button className="btn-link" onClick={sair}>Sair</button>
          </div>
        </header>
        <main className="conteudo">
          <Routes>
            <Route path="/" element={<Lotes />} />
            <Route path="/lotes/:id" element={<Auditoria />} />
            <Route path="/lotes/:id/revisao" element={<Revisao />} />
            <Route path="/lotes/:id/correcao" element={<Correcao />} />
            <Route path="/log" element={<LogAuditoria />} />
            <Route path="/admin/usuarios" element={admin ? <Usuarios /> : <Navigate to="/" />} />
            <Route path="/admin/regras" element={admin ? <Regras /> : <Navigate to="/" />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </main>
        {(trocar || usuario.trocarSenha) && (
          <TrocarSenha obrigatorio={usuario.trocarSenha} aoFechar={() => setTrocar(false)}
            aoTrocar={(u) => { setUsuario(u); setTrocar(false) }} />
        )}
      </div>
    </Ctx.Provider>
  )
}
