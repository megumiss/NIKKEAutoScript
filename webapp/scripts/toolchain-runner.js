const childProcess = require('child_process')
const fs = require('fs')
const path = require('path')

function envValue(env, name) {
  const key = Object.keys(env).find(candidate => candidate.toLowerCase() === name.toLowerCase())
  return key ? env[key] : ''
}

function setEnvValue(env, name, value) {
  for (const key of Object.keys(env)) {
    if (key.toLowerCase() === name.toLowerCase()) delete env[key]
  }
  env[name] = value
}

function rustBinDirectory(env) {
  const executable = process.platform === 'win32' ? 'cargo.exe' : 'cargo'
  const candidates = []
  if (envValue(env, 'CARGO')) candidates.push(path.dirname(envValue(env, 'CARGO')))
  if (envValue(env, 'CARGO_HOME')) candidates.push(path.join(envValue(env, 'CARGO_HOME'), 'bin'))
  if (envValue(env, 'USERPROFILE')) candidates.push(path.join(envValue(env, 'USERPROFILE'), '.cargo', 'bin'))
  if (envValue(env, 'HOME')) candidates.push(path.join(envValue(env, 'HOME'), '.cargo', 'bin'))
  return candidates.find(candidate => {
    const cargo = path.join(candidate, executable)
    return fs.existsSync(cargo) && fs.statSync(cargo).isFile()
  })
}

function environmentWithRust() {
  const env = { ...process.env }
  const rustBin = rustBinDirectory(env)
  if (rustBin) {
    const current = envValue(env, 'PATH')
    const entries = current.split(path.delimiter)
    const normalize = value => path.resolve(value).toLowerCase()
    if (!entries.some(entry => entry && normalize(entry) === normalize(rustBin))) {
      setEnvValue(env, 'PATH', `${rustBin}${path.delimiter}${current}`)
    }
  }
  return env
}

function executableOnPath(name, env) {
  return envValue(env, 'PATH').split(path.delimiter).some(entry => {
    if (!entry) return false
    const clean = entry.replace(/^"|"$/g, '')
    return fs.existsSync(path.join(clean, name))
  })
}

function findVcvars(env) {
  const roots = [envValue(env, 'ProgramFiles(x86)'), envValue(env, 'ProgramFiles')].filter(Boolean)
  const installer = roots
    .map(root => path.join(root, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe'))
    .find(candidate => fs.existsSync(candidate))
  if (installer) {
    const result = childProcess.spawnSync(installer, [
      '-latest', '-products', '*', '-requires',
      'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath',
    ], { encoding: 'utf8', env })
    const installation = result.status === 0 ? result.stdout.trim() : ''
    if (installation) {
      const vcvars = path.join(installation, 'VC', 'Auxiliary', 'Build', 'vcvars64.bat')
      if (fs.existsSync(vcvars)) return vcvars
    }
  }
  for (const root of roots) {
    for (const version of ['2022', '2019', '2017']) {
      const base = path.join(root, 'Microsoft Visual Studio', version)
      if (!fs.existsSync(base)) continue
      for (const edition of fs.readdirSync(base)) {
        const vcvars = path.join(base, edition, 'VC', 'Auxiliary', 'Build', 'vcvars64.bat')
        if (fs.existsSync(vcvars)) return vcvars
      }
    }
  }
  return ''
}

function hasWindowsSdk(env) {
  return envValue(env, 'LIB').split(path.delimiter).some(entry => (
    entry && fs.existsSync(path.join(entry, 'kernel32.lib'))
  ))
}

function environmentWithMsvc(env) {
  if (process.platform !== 'win32') return env
  if (executableOnPath('link.exe', env) && hasWindowsSdk(env)) return env
  const vcvars = findVcvars(env)
  if (!vcvars) return env
  const result = childProcess.spawnSync(
    `call "${vcvars}" >nul 2>nul && set`,
    [],
    { encoding: 'utf8', env, shell: 'cmd.exe' },
  )
  if (result.status !== 0) return env
  const merged = { ...env }
  for (const line of result.stdout.split(/\r?\n/)) {
    const separator = line.indexOf('=')
    if (separator > 0) setEnvValue(merged, line.slice(0, separator), line.slice(separator + 1))
  }
  return merged
}

const [tool, ...args] = process.argv.slice(2)
const env = environmentWithMsvc(environmentWithRust())
let executable
let commandArgs

if (tool === 'cargo') {
  executable = process.platform === 'win32' ? 'cargo.exe' : 'cargo'
  commandArgs = args
} else if (tool === 'tauri') {
  executable = process.execPath
  commandArgs = [path.resolve(__dirname, '../node_modules/@tauri-apps/cli/tauri.js'), ...args]
} else {
  console.error(`Unknown tool: ${tool || '(missing)'}`)
  process.exit(2)
}

if (process.platform === 'win32' && !executableOnPath('link.exe', env)) {
  console.error('MSVC link.exe was not found. Install Visual Studio C++ Build Tools.')
  process.exit(1)
}
if (process.platform === 'win32' && !hasWindowsSdk(env)) {
  console.error('Windows SDK libraries were not found. Install the Windows 10/11 SDK component (kernel32.lib).')
  process.exit(1)
}

const result = childProcess.spawnSync(executable, commandArgs, { env, stdio: 'inherit' })
if (result.error) {
  if (result.error.code === 'ENOENT') {
    console.error('Cargo was not found. Install stable Rust with rustup or set CARGO_HOME/PATH.')
  } else {
    console.error(result.error.message)
  }
  process.exit(1)
}
process.exit(result.status === null ? 1 : result.status)
