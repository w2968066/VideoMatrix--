export interface BackendIdentity {
  port: number
  version: string
  instance: string
}

export function validateIdentity(value: unknown, version: string, instance: string): asserts value is BackendIdentity {
  const data = value as Partial<BackendIdentity> | null
  if (!data || data.version !== version || data.instance !== instance) {
    throw new Error('前后端版本或启动身份不一致，请重新安装完整安装包。')
  }
  if (!Number.isInteger(data.port) || data.port! < 1 || data.port! > 65535) {
    throw new Error('后端返回了无效端口。')
  }
}
