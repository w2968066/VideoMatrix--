import { spawn } from 'child_process'

// Native moves with exclusive destinations. fs.rename can overwrite on some platforms.
export async function renameWithoutReplace(source: string, destination: string): Promise<void> {
  let executable: string, args: string[]
  const env = { ...process.env, VIDEOMATRIX_RENAME_SOURCE: source, VIDEOMATRIX_RENAME_DESTINATION: destination }
  if (process.platform === 'win32') {
    executable = 'powershell.exe'
    args = ['-NoProfile', '-NonInteractive', '-Command',
      '$ErrorActionPreference="Stop"; $source=$env:VIDEOMATRIX_RENAME_SOURCE; $destination=$env:VIDEOMATRIX_RENAME_DESTINATION; if ((Get-Item -LiteralPath $source -Force).PSIsContainer) { [System.IO.Directory]::Move($source,$destination) } else { [System.IO.File]::Move($source,$destination) }']
  } else if (process.platform === 'darwin') {
    executable = '/usr/bin/osascript'
    args = ['-l', 'JavaScript', '-e',
      'ObjC.import("Foundation"); function run(argv) { var error = Ref(); if (!$.NSFileManager.defaultManager.moveItemAtPathToPathError(argv[0], argv[1], error)) { throw Error(ObjC.unwrap(error[0].localizedDescription)); } }', source, destination]
  } else throw new Error('此系统暂不支持安全改名，请使用系统文件管理器。')
  await new Promise<void>((resolve, reject) => {
    const child = spawn(executable, args, { env, windowsHide: true, stdio: 'ignore' })
    child.on('error', reject)
    child.on('close', code => code === 0 ? resolve() : reject(new Error('改名失败：目标可能已存在、被占用或无写入权限；未覆盖已有项目。')))
  })
}
