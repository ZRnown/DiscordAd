import { useEffect, useState, type ReactNode } from 'react'
import { HashRouter, Routes, Route, NavLink } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Users, FileText, Send } from 'lucide-react'

import AccountsPage from './pages/AccountsPage'
import ContentsPage from './pages/ContentsPage'
import AutoSenderPage from './pages/AutoSenderPage'
import LicenseGate from './components/LicenseGate'

const API_BASE = 'http://127.0.0.1:5001/api'

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms)
    promise
      .then((value) => {
        clearTimeout(timer)
        resolve(value)
      })
      .catch((error) => {
        clearTimeout(timer)
        reject(error)
      })
  })
}

function App() {
  const [licenseActive, setLicenseActive] = useState(
    () => localStorage.getItem('licenseActivated') === 'true'
  )

  const checkServerAndLicense = async () => {
    try {
      const healthRes = await withTimeout(fetch(`${API_BASE}/health`, { method: 'GET' }), 5000)
      if (!healthRes.ok) {
        throw new Error('Backend not ready')
      }

      const res = await withTimeout(fetch(`${API_BASE}/license/status`), 5000)
      const data = await res.json()
      if (data.success && data.activated) {
        if (!licenseActive) {
          setLicenseActive(true)
          localStorage.setItem('licenseActivated', 'true')
        }
      } else if (data.success && !data.activated && licenseActive) {
        setLicenseActive(false)
        localStorage.removeItem('licenseActivated')
      }
    } catch (error) {
    }
  }

  useEffect(() => {
    checkServerAndLicense()
    const timer = setInterval(() => {
      checkServerAndLicense()
    }, 3000)
    return () => clearInterval(timer)
  }, [licenseActive])

  let content: ReactNode
  if (!licenseActive) {
    content = <LicenseGate onActivated={checkServerAndLicense} />
  } else {
    content = (
      <HashRouter>
        <div className="flex min-h-screen bg-gray-50">
          {/* 侧边栏 */}
          <nav className="w-64 bg-white border-r border-gray-200 p-4 flex flex-col h-screen">
            <div className="mb-8">
              <h1 className="text-xl font-bold text-gray-800">Discord 营销</h1>
              <p className="text-sm text-gray-500">自动发送系统</p>
            </div>

            <ul className="space-y-2 flex-1">
              <li>
                <NavLink
                  to="/"
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-4 py-2 rounded-lg transition-colors ${
                      isActive
                        ? 'bg-blue-50 text-blue-600'
                        : 'text-gray-600 hover:bg-gray-100'
                    }`
                  }
                >
                  <Send size={20} />
                  <span>自动发送</span>
                </NavLink>
              </li>
              <li>
                <NavLink
                  to="/accounts"
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-4 py-2 rounded-lg transition-colors ${
                      isActive
                        ? 'bg-blue-50 text-blue-600'
                        : 'text-gray-600 hover:bg-gray-100'
                    }`
                  }
                >
                  <Users size={20} />
                  <span>账号管理</span>
                </NavLink>
              </li>
              <li>
                <NavLink
                  to="/contents"
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-4 py-2 rounded-lg transition-colors ${
                      isActive
                        ? 'bg-blue-50 text-blue-600'
                        : 'text-gray-600 hover:bg-gray-100'
                    }`
                  }
                >
                  <FileText size={20} />
                  <span>内容管理</span>
                </NavLink>
              </li>
            </ul>

            <div className="mt-6 text-xs text-gray-500 space-y-1 text-center flex flex-col items-center">
              <p className="text-gray-700 font-medium">技术支持</p>
              <p>微信: OceanSeaWang</p>
              <p>Discord: zrnown</p>
            </div>
          </nav>

          {/* 主内容区 */}
          <main className="flex-1 p-6 overflow-auto h-screen">
            <Routes>
              <Route path="/" element={<AutoSenderPage />} />
              <Route path="/accounts" element={<AccountsPage />} />
              <Route path="/contents" element={<ContentsPage />} />
            </Routes>
          </main>
        </div>
      </HashRouter>
    )
  }

  return (
    <>
      {content}
      <Toaster position="top-right" richColors />
    </>
  )
}

export default App
