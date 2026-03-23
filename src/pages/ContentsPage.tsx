import { useState, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { Plus, Trash2, Edit2, RefreshCw, Image, X, Upload } from 'lucide-react'
import { API_BASE } from '../lib/api'
import {
  ContentSendMode,
  getContentSendModeMeta
} from '../lib/contentSendMode'

interface Content {
  id: number
  title: string
  send_mode?: ContentSendMode
  forum_post_title?: string | null
  forum_tags?: string[]
  text_content: string
  image_paths: string[]
  created_at: string
  updated_at: string
}

const parseForumTagsInput = (value: string) => {
  const rawItems = value.split(/[\n,]+/)
  const normalizedTags: string[] = []
  const seen = new Set<string>()

  rawItems.forEach((item) => {
    const cleanItem = item.trim()
    if (!cleanItem) {
      return
    }
    const dedupeKey = cleanItem.toLowerCase()
    if (seen.has(dedupeKey)) {
      return
    }
    seen.add(dedupeKey)
    normalizedTags.push(cleanItem)
  })

  return normalizedTags
}

export default function ContentsPage() {
  const [contents, setContents] = useState<Content[]>([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editingContent, setEditingContent] = useState<Content | null>(null)
  const [formTitle, setFormTitle] = useState('')
  const [formSendMode, setFormSendMode] = useState<'direct' | 'post'>('direct')
  const [formForumPostTitle, setFormForumPostTitle] = useState('')
  const [formForumTagsText, setFormForumTagsText] = useState('')
  const [formText, setFormText] = useState('')
  const [formImages, setFormImages] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const fetchContents = async () => {
    try {
      const res = await fetch(`${API_BASE}/contents`)
      const data = await res.json()
      if (data.success) {
        setContents(data.contents || [])
      }
    } catch (error) {
      toast.error('获取内容列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchContents()
  }, [])

  const openAddModal = () => {
    setEditingContent(null)
    setFormTitle('')
    setFormSendMode('direct')
    setFormForumPostTitle('')
    setFormForumTagsText('')
    setFormText('')
    setFormImages([])
    setShowModal(true)
  }

  const openEditModal = (content: Content) => {
    setEditingContent(content)
    setFormTitle(content.title)
    setFormSendMode(content.send_mode === 'post' ? 'post' : 'direct')
    setFormForumPostTitle(content.forum_post_title || '')
    setFormForumTagsText((content.forum_tags || []).join('\n'))
    setFormText(content.text_content || '')
    setFormImages(content.image_paths || [])
    setShowModal(true)
  }

  const closeModal = () => {
    setShowModal(false)
    setEditingContent(null)
    setFormTitle('')
    setFormSendMode('direct')
    setFormForumPostTitle('')
    setFormForumTagsText('')
    setFormText('')
    setFormImages([])
  }

  const handleSave = async () => {
    if (!formTitle.trim()) {
      toast.error('请输入标题')
      return
    }

    const parsedForumTags = formSendMode === 'post' ? parseForumTagsInput(formForumTagsText) : []

    try {
      if (editingContent) {
        // 更新
        const res = await fetch(`${API_BASE}/contents/${editingContent.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: formTitle,
            send_mode: formSendMode,
            forum_post_title: formForumPostTitle,
            forum_tags: parsedForumTags,
            text_content: formText,
            image_paths: formImages
          })
        })
        const data = await res.json()
        if (data.success) {
          toast.success('内容更新成功')
          closeModal()
          fetchContents()
        } else {
          toast.error(data.error || '更新失败')
        }
      } else {
        // 新增
        const res = await fetch(`${API_BASE}/contents`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: formTitle,
            send_mode: formSendMode,
            forum_post_title: formForumPostTitle,
            forum_tags: parsedForumTags,
            text_content: formText,
            image_paths: formImages
          })
        })
        const data = await res.json()
        if (data.success) {
          toast.success('内容添加成功')
          closeModal()
          fetchContents()
        } else {
          toast.error(data.error || '添加失败')
        }
      }
    } catch (error) {
      toast.error('保存失败')
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('确定要删除这条内容吗？')) return

    try {
      const res = await fetch(`${API_BASE}/contents/${id}`, { method: 'DELETE' })
      const data = await res.json()
      if (data.success) {
        toast.success('内容已删除')
        fetchContents()
      } else {
        toast.error(data.error || '删除失败')
      }
    } catch (error) {
      toast.error('删除失败')
    }
  }

  const handleUploadImage = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    // 如果是新建内容，需要先创建内容获取ID
    let contentId = editingContent?.id
    if (!contentId) {
      if (!formTitle.trim()) {
        toast.error('请先输入标题')
        return
      }
      // 先创建内容
      try {
        const parsedForumTags = formSendMode === 'post' ? parseForumTagsInput(formForumTagsText) : []
        const res = await fetch(`${API_BASE}/contents`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: formTitle,
            send_mode: formSendMode,
            forum_post_title: formForumPostTitle,
            forum_tags: parsedForumTags,
            text_content: formText,
            image_paths: []
          })
        })
        const data = await res.json()
        if (data.success && data.id) {
          contentId = data.id
          setEditingContent({ ...editingContent, id: contentId } as Content)
        } else {
          toast.error('创建内容失败')
          return
        }
      } catch (error) {
        toast.error('创建内容失败')
        return
      }
    }

    setUploading(true)
    for (const file of Array.from(files)) {
      const formData = new FormData()
      formData.append('file', file)

      try {
        const res = await fetch(`${API_BASE}/contents/${contentId}/upload`, {
          method: 'POST',
          body: formData
        })
        const data = await res.json()
        if (data.success && data.filename) {
          setFormImages((prev) => [...prev, data.filename])
        } else {
          toast.error(data.error || '上传失败')
        }
      } catch (error) {
        toast.error('上传失败')
      }
    }
    setUploading(false)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const removeImage = (index: number) => {
    setFormImages((prev) => prev.filter((_, i) => i !== index))
  }

  const formSendModeMeta = getContentSendModeMeta(formSendMode)

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="animate-spin text-gray-400" size={32} />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-800">内容管理</h2>
          <p className="text-gray-500">管理自动发送的内容（文字+图片）</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchContents}
            className="flex items-center gap-2 px-4 py-2 text-gray-600 bg-white border rounded-lg hover:bg-gray-50"
          >
            <RefreshCw size={18} />
            刷新
          </button>
          <button
            onClick={openAddModal}
            className="flex items-center gap-2 px-4 py-2 text-white bg-blue-600 rounded-lg hover:bg-blue-700"
          >
            <Plus size={18} />
            添加内容
          </button>
        </div>
      </div>

      {/* 内容列表 */}
      {contents.length === 0 ? (
        <div className="bg-white rounded-lg border p-12 text-center">
          <Image className="mx-auto text-gray-300 mb-4" size={48} />
          <p className="text-gray-500">暂无内容，点击"添加内容"创建</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {contents.map((content) => {
            const modeMeta = getContentSendModeMeta(content.send_mode)

            return (
              <div key={content.id} className="bg-white rounded-lg border p-4 hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start gap-2">
                      <h3 className="font-medium text-gray-800 truncate flex-1">{content.title}</h3>
                      <span
                        className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${modeMeta.badgeClass}`}
                      >
                        {modeMeta.label}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      目标要求：{modeMeta.targetLabel}
                    </p>
                  </div>
                  <div className="flex gap-1 ml-2">
                    <button
                      onClick={() => openEditModal(content)}
                      className="p-1 text-gray-400 hover:text-blue-600"
                      title="编辑"
                    >
                      <Edit2 size={16} />
                    </button>
                    <button
                      onClick={() => handleDelete(content.id)}
                      className="p-1 text-gray-400 hover:text-red-600"
                      title="删除"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
                <p className="text-xs text-gray-500 mb-1">
                  发送说明：{modeMeta.modeHint}
                </p>
                {content.send_mode === 'post' && (
                  <>
                    <p className="text-xs text-gray-500 mb-1">
                      帖子标题：{content.forum_post_title?.trim() || '使用内容标题'}
                    </p>
                    <p className="text-xs text-gray-500 mb-2">
                      帖子标签：{content.forum_tags && content.forum_tags.length > 0 ? content.forum_tags.join('、') : '不设置'}
                    </p>
                  </>
                )}
                {content.text_content && (
                  <p className="text-sm text-gray-600 mb-2 line-clamp-3">{content.text_content}</p>
                )}
                {content.image_paths && content.image_paths.length > 0 && (
                  <div className="flex gap-1 flex-wrap">
                    {content.image_paths.slice(0, 4).map((img, idx) => (
                      <img
                        key={idx}
                        src={`${API_BASE}/content_image/${img}`}
                        alt=""
                        className="w-12 h-12 object-cover rounded"
                      />
                    ))}
                    {content.image_paths.length > 4 && (
                      <div className="w-12 h-12 bg-gray-100 rounded flex items-center justify-center text-xs text-gray-500">
                        +{content.image_paths.length - 4}
                      </div>
                    )}
                  </div>
                )}
                <p className="text-xs text-gray-400 mt-2">
                  {new Date(content.created_at).toLocaleString()}
                </p>
              </div>
            )
          })}
        </div>
      )}

      {/* 添加/编辑弹窗 */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between p-4 border-b">
              <h3 className="text-lg font-medium">
                {editingContent ? '编辑内容' : '添加内容'}
              </h3>
              <button onClick={closeModal} className="text-gray-400 hover:text-gray-600">
                <X size={20} />
              </button>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  标题 <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  placeholder="输入内容标题"
                  className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  发送方式
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setFormSendMode('direct')}
                    className={`px-3 py-2 rounded-lg border text-sm ${
                      formSendMode === 'direct'
                        ? 'bg-blue-50 border-blue-300 text-blue-700'
                        : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    直接发送
                  </button>
                  <button
                    type="button"
                    onClick={() => setFormSendMode('post')}
                    className={`px-3 py-2 rounded-lg border text-sm ${
                      formSendMode === 'post'
                        ? 'bg-blue-50 border-blue-300 text-blue-700'
                        : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    新建帖子
                  </button>
                </div>
                <div className={`mt-2 rounded-lg border px-3 py-2 text-xs ${formSendModeMeta.panelClass}`}>
                  <p className="font-medium">当前方式：{formSendModeMeta.label}</p>
                  <p className="mt-1 leading-5">{formSendModeMeta.targetHint}</p>
                  <p className="mt-1 leading-5">
                    {formSendMode === 'post'
                      ? '帖子标题默认取内容标题，也可以在下面单独设置；帖子标签可留空。'
                      : '自动发送时目标可以填写普通频道 ID，或者已存在帖子的链接/ID。'}
                  </p>
                </div>
              </div>
              {formSendMode === 'post' && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      帖子标题
                    </label>
                    <input
                      type="text"
                      value={formForumPostTitle}
                      onChange={(e) => setFormForumPostTitle(e.target.value)}
                      placeholder="留空则使用内容标题"
                      className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      帖子标签
                    </label>
                    <textarea
                      value={formForumTagsText}
                      onChange={(e) => setFormForumTagsText(e.target.value)}
                      placeholder="可选，填写标签名称或标签 ID，多个用逗号或换行分隔"
                      rows={3}
                      className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    />
                    <p className="mt-1 text-xs text-gray-500">
                      留空则不挂标签。如果目标论坛频道设置了必须选择标签，这里至少填一个可用标签。
                    </p>
                  </div>
                </>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  文字内容
                </label>
                <textarea
                  value={formText}
                  onChange={(e) => setFormText(e.target.value)}
                  placeholder="输入要发送的文字内容"
                  rows={4}
                  className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  图片
                </label>
                <div className="flex flex-wrap gap-2 mb-2">
                  {formImages.map((img, idx) => (
                    <div key={idx} className="relative">
                      <img
                        src={`${API_BASE}/content_image/${img}`}
                        alt=""
                        className="w-20 h-20 object-cover rounded"
                      />
                      <button
                        onClick={() => removeImage(idx)}
                        className="absolute -top-1 -right-1 bg-red-500 text-white rounded-full p-0.5"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}
                </div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  onChange={handleUploadImage}
                  className="hidden"
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 border rounded-lg hover:bg-gray-50 disabled:opacity-50"
                >
                  <Upload size={16} />
                  {uploading ? '上传中...' : '上传图片'}
                </button>
              </div>
            </div>
            <div className="flex justify-end gap-2 p-4 border-t">
              <button
                onClick={closeModal}
                className="px-4 py-2 text-gray-600 border rounded-lg hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={handleSave}
                className="px-4 py-2 text-white bg-blue-600 rounded-lg hover:bg-blue-700"
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
