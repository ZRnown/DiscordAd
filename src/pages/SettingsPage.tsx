import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Copy, FolderOpen, RefreshCw, Save } from 'lucide-react'
import { open } from '@tauri-apps/api/dialog'
import { API_BASE } from '../lib/api'

interface StorageState {
  data_dir: string
  default_data_dir: string
  database_path: string
  content_images_dir: string
  storage_config_path: string
}

const copyText = async (value: string) => {
  if (!value) {
    return false
  }
  try {
    await navigator.clipboard.writeText(value)
    return true
  } catch (error) {
    return false
  }
}

export default function SettingsPage() {
  const [storage, setStorage] = useState<StorageState | null>(null)
  const [draftPath, setDraftPath] = useState('')
  const [migrateData, setMigrateData] = useState(true)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const fetchStorage = async () => {
    try {
      const res = await fetch(`${API_BASE}/storage`)
      const data = await res.json()
      if (data.success) {
        setStorage(data)
        setDraftPath(data.data_dir || '')
      } else {
        toast.error(data.error || '获取数据配置失败')
      }
    } catch (error) {
      toast.error('获取数据配置失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStorage()
  }, [])

  const handlePickDirectory = async () => {
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        defaultPath: draftPath || storage?.data_dir,
        title: '选择数据目录'
      })
      if (typeof selected === 'string' && selected.trim()) {
        setDraftPath(selected)
      }
    } catch (error) {
      toast.error('打开目录选择器失败')
    }
  }

  const handleCopy = async (value: string, label: string) => {
    const ok = await copyText(value)
    if (ok) {
      toast.success(`${label}已复制`)
      return
    }
    toast.error('复制失败，请手动复制')
  }

  const handleSave = async () => {
    const nextPath = draftPath.trim()
    if (!nextPath) {
      toast.error('请输入数据目录')
      return
    }

    setSaving(true)
    try {
      const res = await fetch(`${API_BASE}/storage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          data_dir: nextPath,
          migrate: migrateData
        })
      })
      const data = await res.json()
      if (data.success) {
        setStorage(data)
        setDraftPath(data.data_dir || nextPath)
        toast.success(migrateData ? '数据目录已切换，旧数据已迁移' : '数据目录已切换')
      } else {
        toast.error(data.error || '保存失败')
      }
    } catch (error) {
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="py-12 text-center text-gray-500">加载中...</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-gray-800">数据设置</h2>
        <p className="mt-1 text-gray-500">
          数据库、上传图片和许可证文件都会写到这里。切换目录后，你可以直接复制整个文件夹做备份或迁移。
        </p>
      </div>

      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-800">当前使用位置</h3>
            <p className="mt-1 text-sm text-gray-500">下面这三个路径会随数据目录一起切换。</p>
          </div>
          <button
            onClick={fetchStorage}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
          >
            <RefreshCw size={16} />
            刷新
          </button>
        </div>

        <div className="mt-5 space-y-4">
          {[
            ['数据目录', storage?.data_dir || ''],
            ['数据库文件', storage?.database_path || ''],
            ['图片目录', storage?.content_images_dir || '']
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium text-gray-700">{label}</p>
                <button
                  onClick={() => handleCopy(value, label)}
                  className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
                >
                  <Copy size={14} />
                  复制
                </button>
              </div>
              <p className="mt-2 break-all font-mono text-sm text-gray-600">{value}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h3 className="text-lg font-semibold text-gray-800">切换数据目录</h3>
        <p className="mt-1 text-sm text-gray-500">
          建议先新建一个独立文件夹。勾选迁移后，现有数据库和图片会复制过去，新目录会立即生效。
        </p>

        <div className="mt-5 space-y-4">
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700">目标目录</label>
            <div className="flex gap-3">
              <input
                type="text"
                value={draftPath}
                onChange={(event) => setDraftPath(event.target.value)}
                placeholder="例如 D:\\DiscordAutoSenderData"
                className="flex-1 rounded-lg border border-gray-300 px-4 py-3 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
              />
              <button
                onClick={handlePickDirectory}
                className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-4 py-3 text-sm text-gray-700 hover:bg-gray-50"
              >
                <FolderOpen size={16} />
                选择目录
              </button>
            </div>
          </div>

          <label className="flex items-start gap-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
            <input
              type="checkbox"
              checked={migrateData}
              onChange={(event) => setMigrateData(event.target.checked)}
              className="mt-1 h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            />
            <span className="text-sm text-gray-700">
              迁移现有数据。关闭后只会切换到新目录，适合你想从一个空目录重新开始的时候。
            </span>
          </label>

          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
            自动发送任务运行中时不能切换目录。切换完成后，后续新增账号、内容和图片都会写入新位置。
          </div>

          <div className="flex justify-end">
            <button
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-3 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Save size={16} />
              {saving ? '保存中...' : '保存设置'}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
