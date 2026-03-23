export type ContentSendMode = 'direct' | 'post'

type ContentSendModeMeta = {
  label: string
  badgeClass: string
  panelClass: string
  targetLabel: string
  targetHint: string
  modeHint: string
}

const MODE_META: Record<ContentSendMode, ContentSendModeMeta> = {
  direct: {
    label: '直接发送',
    badgeClass: 'border border-slate-200 bg-slate-100 text-slate-700',
    panelClass: 'border-slate-200 bg-slate-50 text-slate-700',
    targetLabel: '普通频道 / 已存在帖子链接或 ID',
    targetHint: '消息会直接发到普通频道，或者发到你填写的已存在帖子里。',
    modeHint: '适合普通频道或已存在帖子'
  },
  post: {
    label: '新建帖子',
    badgeClass: 'border border-indigo-200 bg-indigo-50 text-indigo-700',
    panelClass: 'border-indigo-200 bg-indigo-50 text-indigo-700',
    targetLabel: '论坛频道 ID',
    targetHint: '系统会在这个论坛频道下新建帖子，不需要额外再选 Discord 帖子类型；帖子标签可选。',
    modeHint: '适合论坛频道发新帖子'
  }
}

export const normalizeContentSendMode = (
  sendMode?: ContentSendMode
): ContentSendMode => {
  return sendMode === 'post' ? 'post' : 'direct'
}

export const getContentSendModeMeta = (sendMode?: ContentSendMode) => {
  return MODE_META[normalizeContentSendMode(sendMode)]
}

export const normalizeForumTags = (forumTags: unknown): string[] => {
  if (Array.isArray(forumTags)) {
    return forumTags
      .map((tag) => String(tag || '').trim())
      .filter(Boolean)
  }

  if (typeof forumTags === 'string') {
    const text = forumTags.trim()
    if (!text) {
      return []
    }

    try {
      const parsed = JSON.parse(text)
      if (Array.isArray(parsed)) {
        return parsed
          .map((tag) => String(tag || '').trim())
          .filter(Boolean)
      }
    } catch (error) {
    }

    return text
      .split(/[\n,]+/)
      .map((tag) => tag.trim())
      .filter(Boolean)
  }

  return []
}
