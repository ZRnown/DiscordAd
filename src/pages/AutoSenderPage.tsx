import { useState, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { Play, Square, RefreshCw, CheckCircle, Circle, ToggleLeft, ToggleRight } from 'lucide-react'

interface Content {
  id: number
  title: string
  text_content: string
  image_paths: string[]
}

interface Account {
  id: number
  username: string
  is_online: boolean
  channel_ids?: string[]
}

interface TaskStatus {
  is_running: boolean
  is_paused: boolean
  content_ids: number[]
  account_ids: number[]
  channel_ids?: string[]
  rotation_mode: boolean
  repeat_mode: boolean
  total_contents: number
  sent_count: number
  current_content: string | null
  current_account: string | null
  started_at: string | null
  last_sent_at: string | null
  error: string | null
}

const API_BASE = 'http://127.0.0.1:5001/api'
const CHANNEL_IDS_STORAGE_KEY = 'discord-auto-sender.channel-ids.v1'

const loadChannelIds = () => {
  if (typeof window === 'undefined') {
    return ''
  }
  try {
    return localStorage.getItem(CHANNEL_IDS_STORAGE_KEY) ?? ''
  } catch (error) {
    return ''
  }
}

const saveChannelIds = (value: string) => {
  if (typeof window === 'undefined') {
    return
  }
  try {
    if (value) {
      localStorage.setItem(CHANNEL_IDS_STORAGE_KEY, value)
    } else {
      localStorage.removeItem(CHANNEL_IDS_STORAGE_KEY)
    }
  } catch (error) {
  }
}

export default function AutoSenderPage() {
  const [contents, setContents] = useState<Content[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [status, setStatus] = useState<TaskStatus | null>(null)
  const previousStatusRef = useRef<TaskStatus | null>(null)

  // 表单状态
  const [selectedContents, setSelectedContents] = useState<number[]>([])
  const [selectedAccounts, setSelectedAccounts] = useState<number[]>([])
  const [rotationMode, setRotationMode] = useState(true)
  const [repeatMode, setRepeatMode] = useState(true)
  const [sendInterval, setSendInterval] = useState('60')
  const [channelIds, setChannelIds] = useState(loadChannelIds)
  const [loading, setLoading] = useState(false)
  const channelInitRef = useRef(false)

  // 获取内容和账号数据
  const fetchData = async () => {
    try {
      const [contentsRes, accountsRes, statusRes] = await Promise.all([
        fetch(`${API_BASE}/contents`),
        fetch(`${API_BASE}/accounts`),
        fetch(`${API_BASE}/sender/status`)
      ])

      const contentsData = await contentsRes.json()
      const accountsData = await accountsRes.json()
      const statusData = await statusRes.json()

      if (contentsData.success) {
        setContents(contentsData.contents || [])
      }
      if (accountsData.success) {
        // 只显示在线账号（不再要求配置频道）
        const onlineAccounts = (accountsData.accounts || []).filter(
          (a: Account) => a.is_online
        )
        setAccounts(onlineAccounts)
      }
      if (statusData.success) {
        setStatus(statusData.status)
      }
    } catch (error) {
      console.error('获取数据失败:', error)
    }
  }

  useEffect(() => {
    fetchData()
    const timer = window.setInterval(fetchData, 3000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (accounts.length === 0) {
      setSelectedAccounts([])
      return
    }
    setSelectedAccounts((prev) => prev.filter((id) => accounts.some((a) => a.id === id)))
  }, [accounts])

  useEffect(() => {
    if (!status) {
      previousStatusRef.current = status
      return
    }
    const previousStatus = previousStatusRef.current
    const repeatEnabled = status.repeat_mode ?? false
    if (previousStatus?.is_running && !status.is_running) {
      if (status.error) {
        toast.error(`任务异常: ${status.error}`)
      } else if (status.is_paused) {
        toast.success('任务已暂停')
      } else if (!repeatEnabled && status.total_contents > 0 && status.sent_count >= status.total_contents) {
        toast.success('任务已完成')
      } else {
        toast.success('任务已停止')
      }
    }
    previousStatusRef.current = status
  }, [status])

  useEffect(() => {
    saveChannelIds(channelIds.trim())
  }, [channelIds])

  useEffect(() => {
    if (channelInitRef.current || !status) {
      return
    }
    if (channelIds.trim()) {
      channelInitRef.current = true
      return
    }
    if (status.channel_ids && status.channel_ids.length > 0) {
      setChannelIds(status.channel_ids.join('\n'))
      channelInitRef.current = true
      return
    }
  }, [status, channelIds])

  const toggleContent = (id: number) => {
    setSelectedContents((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }

  const toggleAccount = (id: number) => {
    if (!rotationMode && selectedAccounts.length >= 1 && !selectedAccounts.includes(id)) {
      toast.error('单账号模式只能选择一个账号')
      return
    }
    setSelectedAccounts((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }

  const allContentsSelected =
    contents.length > 0 && contents.every((c) => selectedContents.includes(c.id))

  const toggleSelectAllContents = () => {
    if (allContentsSelected) {
      setSelectedContents([])
    } else {
      setSelectedContents(contents.map((c) => c.id))
    }
  }

  const allAccountsSelected =
    accounts.length > 0 && accounts.every((a) => selectedAccounts.includes(a.id))

  const toggleSelectAllAccounts = () => {
    if (!rotationMode) {
      toast.error('单账号模式只能选择一个账号')
      return
    }
    if (allAccountsSelected) {
      setSelectedAccounts([])
    } else {
      setSelectedAccounts(accounts.map((a) => a.id))
    }
  }

  const handleRotationModeChange = (mode: boolean) => {
    setRotationMode(mode)
    if (!mode && selectedAccounts.length > 1) {
      setSelectedAccounts([selectedAccounts[0]])
    }
  }

  const handleStart = async () => {
    if (selectedContents.length === 0) {
      toast.error('请选择至少一条内容')
      return
    }
    if (selectedAccounts.length === 0) {
      toast.error('请选择至少一个账号')
      return
    }
    const channels = channelIds
      .split(/[\n,\s]+/)
      .map((c) => c.trim())
      .filter(Boolean)
    if (channels.length === 0) {
      toast.error('请输入至少一个频道ID')
      return
    }

    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/sender/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          contentIds: selectedContents,
          accountIds: selectedAccounts,
          channelIds: channels,
          rotationMode,
          repeatMode,
          interval: parseInt(sendInterval)
        })
      })
      const data = await res.json()
      if (data.success) {
        toast.success('自动发送已启动')
        fetchData()
      } else {
        toast.error(data.error || '启动失败')
      }
    } catch (error) {
      toast.error('启动失败')
    } finally {
      setLoading(false)
    }
  }

  const handleStop = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/sender/stop`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        toast.success('已停止发送')
        fetchData()
      } else {
        toast.error(data.error || '停止失败')
      }
    } catch (error) {
      toast.error('停止失败')
    } finally {
      setLoading(false)
    }
  }

  const handlePause = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/sender/pause`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        toast.success('任务已暂停')
        fetchData()
      } else {
        toast.error(data.error || '暂停失败')
      }
    } catch (error) {
      toast.error('暂停失败')
    } finally {
      setLoading(false)
    }
  }

  const handleResume = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/sender/resume`, { method: 'POST' })
      const data = await res.json()
      if (data.success) {
        toast.success('任务继续运行中...')
        fetchData()
      } else {
        toast.error(data.error || '继续失败')
      }
    } catch (error) {
      toast.error('继续失败')
    } finally {
      setLoading(false)
    }
  }

  const isRunning = status?.is_running || false
  const isPaused = status?.is_paused || false
  const repeatEnabled = status?.repeat_mode ?? false

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-800">自动发送控制台</h2>
          <p className="text-gray-500">选择内容和账号，启动自动发送任务</p>
        </div>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 px-4 py-2 text-gray-600 bg-white border rounded-lg hover:bg-gray-50"
        >
          <RefreshCw size={18} />
          刷新
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 左侧：内容选择 */}
        <div className="bg-white rounded-lg border p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold text-gray-800">选择内容</h3>
            <button
              type="button"
              onClick={toggleSelectAllContents}
              disabled={isRunning || isPaused}
              className="text-sm text-blue-600 hover:text-blue-700 disabled:opacity-50"
            >
              {allContentsSelected ? '全不选' : '全选'}
            </button>
          </div>

          {contents.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              暂无内容，请先在内容管理页面添加
            </div>
          ) : (
            <div className="space-y-2 max-h-[300px] overflow-y-auto">
              {contents.map((content) => (
                <div
                  key={content.id}
                  onClick={() => !(isRunning || isPaused) && toggleContent(content.id)}
                  className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                    selectedContents.includes(content.id)
                      ? 'bg-blue-50 border-blue-200 border'
                      : 'bg-gray-50 hover:bg-gray-100'
                  } ${isRunning || isPaused ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  {selectedContents.includes(content.id) ? (
                    <CheckCircle size={20} className="text-blue-600 flex-shrink-0" />
                  ) : (
                    <Circle size={20} className="text-gray-400 flex-shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <span className="font-medium text-gray-800 block truncate">
                      {content.title}
                    </span>
                    {content.text_content && (
                      <span className="text-xs text-gray-500 block truncate">
                        {content.text_content.substring(0, 50)}...
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 中间：配置区 */}
        <div className="bg-white rounded-lg border p-6">
          <h3 className="text-lg font-bold text-gray-800 mb-4">任务配置</h3>

          <div className="space-y-4">
            {/* 发送模式 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                发送模式
              </label>
              <div className="flex gap-4">
                <button
                  onClick={() => handleRotationModeChange(true)}
                  disabled={isRunning || isPaused}
                  className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
                    rotationMode
                      ? 'bg-blue-50 border-blue-300 text-blue-700'
                      : 'bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100'
                  } disabled:opacity-50`}
                >
                  <ToggleRight size={18} />
                  轮换模式
                </button>
                <button
                  onClick={() => handleRotationModeChange(false)}
                  disabled={isRunning || isPaused}
                  className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
                    !rotationMode
                      ? 'bg-blue-50 border-blue-300 text-blue-700'
                      : 'bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100'
                  } disabled:opacity-50`}
                >
                  <ToggleLeft size={18} />
                  单账号
                </button>
              </div>
              <p className="mt-1 text-xs text-gray-500">
                {rotationMode ? '多账号轮流发送' : '只用一个账号发送'}
              </p>
            </div>

            {/* 发送方式 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                发送方式
              </label>
              <div className="flex gap-4">
                <button
                  onClick={() => setRepeatMode(true)}
                  disabled={isRunning || isPaused}
                  className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
                    repeatMode
                      ? 'bg-blue-50 border-blue-300 text-blue-700'
                      : 'bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100'
                  } disabled:opacity-50`}
                >
                  <ToggleRight size={18} />
                  循环发送
                </button>
                <button
                  onClick={() => setRepeatMode(false)}
                  disabled={isRunning || isPaused}
                  className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
                    !repeatMode
                      ? 'bg-blue-50 border-blue-300 text-blue-700'
                      : 'bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100'
                  } disabled:opacity-50`}
                >
                  <ToggleLeft size={18} />
                  发送一次
                </button>
              </div>
              <p className="mt-1 text-xs text-gray-500">
                {repeatMode ? '内容发送完会从头开始' : '全部内容发送完后自动停止'}
              </p>
            </div>

            {/* 发送间隔 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                发送间隔 (秒)
              </label>
              <input
                type="number"
                value={sendInterval}
                onChange={(e) => setSendInterval(e.target.value)}
                disabled={isRunning || isPaused}
                min="10"
                max="3600"
                className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
              />
              <p className="mt-1 text-xs text-gray-500">建议设置 60 秒以上</p>
            </div>

            {/* 频道配置 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                发送频道 ID
              </label>
              <textarea
                value={channelIds}
                onChange={(e) => setChannelIds(e.target.value)}
                disabled={isRunning || isPaused}
                placeholder="输入频道ID，每行一个或用逗号分隔"
                rows={3}
                className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 text-sm"
              />
              <p className="mt-1 text-xs text-gray-500">所有账号都会向这些频道发送</p>
            </div>

            {/* 操作按钮 */}
            <div className="pt-4 space-y-2">
              <button
                onClick={handleStart}
                disabled={isRunning || isPaused || loading}
                className="w-full flex items-center justify-center gap-2 px-4 py-2 text-white bg-green-600 rounded-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Play size={18} />
                启动任务
              </button>
              <div className="flex gap-2">
                <button
                  onClick={isPaused ? handleResume : handlePause}
                  disabled={loading || (!isRunning && !isPaused)}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2 text-white bg-amber-500 rounded-lg hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isPaused ? '继续' : '暂停'}
                </button>
                <button
                  onClick={handleStop}
                  disabled={(!isRunning && !isPaused) || loading}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2 text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Square size={18} />
                  停止
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* 右侧：账号选择 */}
        <div className="bg-white rounded-lg border p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold text-gray-800">选择账号</h3>
            {rotationMode && (
              <button
                type="button"
                onClick={toggleSelectAllAccounts}
                disabled={isRunning || isPaused}
                className="text-sm text-blue-600 hover:text-blue-700 disabled:opacity-50"
              >
                {allAccountsSelected ? '全不选' : '全选'}
              </button>
            )}
          </div>

          {accounts.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              暂无可用账号，请先启动账号
            </div>
          ) : (
            <div className="space-y-2 max-h-[300px] overflow-y-auto">
              {accounts.map((account) => (
                <div
                  key={account.id}
                  onClick={() => !(isRunning || isPaused) && toggleAccount(account.id)}
                  className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                    selectedAccounts.includes(account.id)
                      ? 'bg-blue-50 border-blue-200 border'
                      : 'bg-gray-50 hover:bg-gray-100'
                  } ${isRunning || isPaused ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  {selectedAccounts.includes(account.id) ? (
                    <CheckCircle size={20} className="text-blue-600 flex-shrink-0" />
                  ) : (
                    <Circle size={20} className="text-gray-400 flex-shrink-0" />
                  )}
                  <div className="flex-1">
                    <span className="font-medium text-gray-800">
                      {account.username || `账号 ${account.id}`}
                    </span>
                    <span className="ml-2 text-xs text-green-600">在线</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 任务状态 */}
      {status && (
        <div className="bg-white rounded-lg border p-6">
          <h3 className="text-lg font-bold text-gray-800 mb-4">任务状态</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
            <div>
              <p className="text-sm text-gray-500">状态</p>
              <p
                className={`font-medium ${
                  isRunning ? 'text-green-600' : isPaused ? 'text-amber-600' : 'text-gray-600'
                }`}
              >
                {isRunning ? '运行中' : isPaused ? '已暂停' : '已停止'}
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500">{repeatEnabled ? '累计发送' : '进度'}</p>
              <p className="font-medium text-gray-800">
                {repeatEnabled
                  ? status.sent_count
                  : `${status.sent_count} / ${status.total_contents}`}
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500">发送模式</p>
              <p className="font-medium text-gray-800">
                {status.rotation_mode ? '轮换模式' : '单账号模式'} ·{' '}
                {repeatEnabled ? '循环发送' : '发送一次'}
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500">当前内容</p>
              <p className="font-medium text-gray-800 truncate">
                {status.current_content || '-'}
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500">当前账号</p>
              <p className="font-medium text-gray-800">{status.current_account || '-'}</p>
            </div>
            <div>
              <p className="text-sm text-gray-500">最后发送</p>
              <p className="font-medium text-gray-800">
                {status.last_sent_at
                  ? new Date(status.last_sent_at).toLocaleTimeString()
                  : '-'}
              </p>
            </div>
          </div>
          {status.error && (
            <div className="mt-4 p-3 bg-red-50 text-red-600 rounded-lg text-sm">
              {status.error}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
