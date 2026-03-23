import { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { Plus, Trash2, Power, PowerOff, RefreshCw } from 'lucide-react'
import { API_BASE } from '../lib/api'

interface Account {
  id: number
  username: string
  token: string
  status: string
  is_online: boolean
  last_active: string | null
  created_at: string
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [newToken, setNewToken] = useState('')
  const [startingAll, setStartingAll] = useState(false)

  const isAccountOnline = (account: Account) => account.is_online
  const isAccountConnecting = (account: Account) =>
    !account.is_online && account.status === 'connecting'

  const getStatusMeta = (account: Account) => {
    if (isAccountOnline(account)) {
      return {
        label: '在线',
        className: 'bg-green-100 text-green-700'
      }
    }
    if (isAccountConnecting(account)) {
      return {
        label: '启动中',
        className: 'bg-amber-100 text-amber-700'
      }
    }
    return {
      label: '离线',
      className: 'bg-gray-100 text-gray-600'
    }
  }

  const fetchAccountsData = async (silent = false) => {
    try {
      const res = await fetch(`${API_BASE}/accounts`)
      const data = await res.json()
      if (data.success) {
        setAccounts(data.accounts || [])
        return data.accounts || []
      }
    } catch (error) {
      if (!silent) {
        toast.error('获取账号列表失败')
      }
    } finally {
      if (!silent) {
        setLoading(false)
      }
    }
    return []
  }

  const fetchAccounts = async () => {
    await fetchAccountsData()
  }

  useEffect(() => {
    fetchAccounts()
    // 定期刷新状态
    const interval = setInterval(fetchAccounts, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleAddAccount = async () => {
    if (!newToken.trim()) {
      toast.error('请输入 Token')
      return
    }

    try {
      const res = await fetch(`${API_BASE}/accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: newToken })
      })
      const data = await res.json()
      if (data.success) {
        toast.success('账号添加成功')
        setNewToken('')
        setShowAddModal(false)
        fetchAccounts()
      } else {
        toast.error(data.error || '添加失败')
      }
    } catch (error) {
      toast.error('添加账号失败')
    }
  }

  const handleDeleteAccount = async (id: number) => {
    if (!confirm('确定要删除这个账号吗？')) return

    try {
      const res = await fetch(`${API_BASE}/accounts/${id}`, { method: 'DELETE' })
      const data = await res.json()
      if (data.success) {
        toast.success('账号已删除')
        fetchAccounts()
      } else {
        toast.error(data.error || '删除失败')
      }
    } catch (error) {
      toast.error('删除账号失败')
    }
  }

  const handleToggleAccount = async (id: number, isActive: boolean) => {
    const action = isActive ? 'stop' : 'start'
    try {
      const res = await fetch(`${API_BASE}/accounts/${id}/${action}`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        toast.success(isActive ? '账号已停止' : '账号启动中...')
        setTimeout(fetchAccounts, 1000)
      } else {
        toast.error(data.error || '操作失败')
      }
    } catch (error) {
      toast.error('操作失败')
    }
  }

  const handleStartAllAccounts = async () => {
    setStartingAll(true)
    const targetIds = accounts
      .filter((account) => !isAccountOnline(account) && !isAccountConnecting(account))
      .map((account) => account.id)
    if (targetIds.length === 0) {
      if (accounts.some((account) => isAccountConnecting(account))) {
        toast.success('账号启动中，请稍等')
      } else {
        toast.success('所有账号已在线')
      }
      setStartingAll(false)
      return
    }

    const waitForAccountsOnline = async (ids: number[], timeoutMs = 180000) => {
      const start = Date.now()
      let latest: Account[] = []
      while (Date.now() - start < timeoutMs) {
        latest = await fetchAccountsData(true)
        const allOnline = ids.every((id) =>
          latest.some((account) => account.id === id && account.is_online)
        )
        if (allOnline) {
          return { done: true, accounts: latest }
        }
        await new Promise((resolve) => setTimeout(resolve, 1000))
      }
      return { done: false, accounts: latest }
    }

    try {
      const res = await fetch(`${API_BASE}/accounts/start_all`, { method: 'POST' })
      if (res.status === 404) {
        for (const account of accounts.filter((item) => !item.is_online)) {
          try {
            const startRes = await fetch(`${API_BASE}/accounts/${account.id}/start`, {
              method: 'POST'
            })
            const startData = await startRes.json()
            if (!startData.success) {
              continue
            }
          } catch (error) {
            continue
          }
        }
        const result = await waitForAccountsOnline(targetIds)
        const onlineCount = targetIds.filter((id) =>
          result.accounts.some((account) => account.id === id && account.is_online)
        ).length
        if (result.done && onlineCount === targetIds.length) {
          toast.success(`已启动 ${onlineCount} 个账号`)
        } else {
          toast.error(`仍有 ${targetIds.length - onlineCount} 个账号未上线`)
        }
        return
      }

      const data = await res.json()
      if (data.success) {
        const result = await waitForAccountsOnline(targetIds)
        const onlineCount = targetIds.filter((id) =>
          result.accounts.some((account) => account.id === id && account.is_online)
        ).length
        if (result.done && onlineCount === targetIds.length) {
          toast.success(`已启动 ${onlineCount} 个账号`)
        } else {
          toast.error(`仍有 ${targetIds.length - onlineCount} 个账号未上线`)
        }
      } else {
        toast.error(data.error || '一键启动失败')
      }
    } catch (error) {
      toast.error('一键启动失败')
    } finally {
      setStartingAll(false)
      await fetchAccountsData(true)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-800">账号管理</h2>
          <p className="text-gray-500">管理你的 Discord 账号，通过 Token 登录</p>
          {startingAll && (
            <p className="text-sm text-amber-600 mt-1">账号启动中，请稍等...</p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchAccounts}
            className="flex items-center gap-2 px-4 py-2 text-gray-600 bg-white border rounded-lg hover:bg-gray-50"
          >
            <RefreshCw size={18} />
            刷新
          </button>
          <button
            onClick={handleStartAllAccounts}
            disabled={startingAll || accounts.length === 0}
            className="flex items-center gap-2 px-4 py-2 text-green-700 bg-green-50 border border-green-200 rounded-lg hover:bg-green-100 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Power size={18} />
            {startingAll ? '启动中...' : '一键启动'}
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="flex items-center gap-2 px-4 py-2 text-white bg-blue-600 rounded-lg hover:bg-blue-700"
          >
            <Plus size={18} />
            添加账号
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">加载中...</div>
      ) : accounts.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-lg border">
          <p className="text-gray-500">暂无账号，请添加 Discord 账号</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-sm font-medium text-gray-500">用户名</th>
                <th className="px-6 py-3 text-left text-sm font-medium text-gray-500">状态</th>
                <th className="px-6 py-3 text-left text-sm font-medium text-gray-500">Token</th>
                <th className="px-6 py-3 text-left text-sm font-medium text-gray-500">最后活跃</th>
                <th className="px-6 py-3 text-right text-sm font-medium text-gray-500">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {accounts.map((account) => {
                const statusMeta = getStatusMeta(account)
                const isOnline = isAccountOnline(account)
                const isConnecting = isAccountConnecting(account)
                const canStart = !isOnline && !isConnecting
                const canStop = isOnline

                return (
                <tr key={account.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 text-sm font-medium text-gray-900">
                    {account.username || `账号 ${account.id}`}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2 py-1 text-xs rounded-full ${statusMeta.className}`}
                    >
                      {statusMeta.label}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 font-mono">
                    {account.token?.substring(0, 20)}...
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500">
                    {account.last_active || '-'}
                  </td>
                  <td className="px-6 py-4 text-right space-x-2">
                    <button
                      onClick={() => {
                        if (canStop) {
                          handleToggleAccount(account.id, true)
                        } else if (canStart) {
                          handleToggleAccount(account.id, false)
                        }
                      }}
                      disabled={isConnecting}
                      className={`p-2 rounded-lg ${
                        canStop
                          ? 'text-red-600 hover:bg-red-50'
                          : canStart
                            ? 'text-green-600 hover:bg-green-50'
                            : 'text-amber-500 cursor-not-allowed'
                      }`}
                      title={canStop ? '停止' : isConnecting ? '启动中' : '启动'}
                    >
                      {canStop ? <PowerOff size={18} /> : <Power size={18} />}
                    </button>
                    <button
                      onClick={() => handleDeleteAccount(account.id)}
                      className="p-2 text-red-600 rounded-lg hover:bg-red-50"
                      title="删除"
                    >
                      <Trash2 size={18} />
                    </button>
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 添加账号弹窗 */}
      {showAddModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-md">
            <h3 className="text-lg font-bold mb-4">添加 Discord 账号</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Token <span className="text-red-500">*</span>
                </label>
                <input
                  type="password"
                  value={newToken}
                  onChange={(e) => setNewToken(e.target.value)}
                  placeholder="Discord 账号 Token"
                  className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
                <p className="mt-1 text-xs text-gray-500">
                  Token 用于登录 Discord 账号，请妥善保管
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <button
                onClick={() => setShowAddModal(false)}
                className="px-4 py-2 text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200"
              >
                取消
              </button>
              <button
                onClick={handleAddAccount}
                className="px-4 py-2 text-white bg-blue-600 rounded-lg hover:bg-blue-700"
              >
                添加
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
