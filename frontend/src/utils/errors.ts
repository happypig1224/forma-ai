/**
 * 结构化解析后端 API 抛出的异常，向用户呈现精准、友好的中文提示
 */
export function parseApiError(err: any, fallbackMessage: string = '操作失败，请重试'): string {
  // 1. 无响应：网络中断、连接被拒绝或超时
  if (!err?.response) {
    if (err?.code === 'ECONNABORTED' || err?.message?.toLowerCase().includes('timeout')) {
      return '网络请求超时，请检查您的网络连接'
    }
    if (err?.code === 'ERR_NETWORK' || err?.message?.toLowerCase().includes('network')) {
      return '网络连接失败，请检查网络或后端服务是否正常启动'
    }
    return '网络异常，无法连接到服务器'
  }

  const status = err.response.status
  const data = err.response.data
  const detail = data?.detail

  // 2. 409 冲突：资源已存在（如邮箱已被注册）
  if (status === 409) {
    if (typeof detail === 'string') {
      const lower = detail.toLowerCase()
      if (lower.includes('already registered') || lower.includes('exist')) {
        return '该邮箱已被注册，请直接切换至登录'
      }
      return detail
    }
    return '该邮箱已被注册，请直接切换至登录'
  }

  // 3. 401 未授权：密码错误、账号禁用或会话失效
  if (status === 401) {
    if (typeof detail === 'string') {
      const lower = detail.toLowerCase()
      if (lower.includes('invalid') || lower.includes('password') || lower.includes('credential')) {
        return '邮箱或密码错误，请核对后重试'
      }
      if (lower.includes('disabled')) {
        return '该账号已被禁用，请联系管理员'
      }
      if (lower.includes('deleted')) {
        return '该账号已注销'
      }
      if (lower.includes('expired') || lower.includes('revoked') || lower.includes('session')) {
        return '登录状态已失效，请重新登录'
      }
      return detail
    }
    return '邮箱或密码错误，请核对后重试'
  }

  // 4. 403 拒绝访问：权限不足或冷却期限制
  if (status === 403) {
    if (typeof detail === 'string') {
      if (detail.includes('disabling')) {
        return '账号处于注销冷静期中，写操作已被限制'
      }
      if (detail.includes('CSRF') || detail.includes('csrf') || detail.includes('origin')) {
        return '安全请求校验未通过，请刷新页面后重试'
      }
      return detail
    }
    return '没有权限执行此操作'
  }

  // 5. 422 格式参数未通过校验（Pydantic 异常）
  if (status === 422) {
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0]
      const loc = first.loc ? first.loc.join('.') : ''
      if (loc.includes('password')) {
        return '密码长度或格式不符合要求（至少 8 位）'
      }
      if (loc.includes('email')) {
        return '电子邮箱格式不正确，请输入有效的邮箱地址'
      }
      return first.msg || '输入参数校验未通过，请核对'
    }
    return typeof detail === 'string' ? detail : '输入格式有误，请核对后重试'
  }

  // 6. 429 频控防刷限制
  if (status === 429) {
    return '操作过于频繁，请稍候再试'
  }

  // 7. 503 服务不可用（如 Redis 或 MySQL 异常）
  if (status === 503) {
    return typeof detail === 'string' ? detail : '依赖服务暂不可用，请稍候重试'
  }

  // 8. 500+ 服务端内部异常
  if (status >= 500) {
    if (typeof detail === 'string' && detail.trim() && !detail.toLowerCase().includes('internal server error')) {
      return `服务端异常：${detail}`
    }
    return '服务端内部异常，请联系管理员或稍后重试'
  }

  // 9. 自定义字符串 detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }

  return fallbackMessage
}
