const assert = require('assert/strict')
const childProcess = require('child_process')
const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '../..')
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8')

const cargoVersion = read('webapp/src-tauri/Cargo.toml').match(/^version = "([^"]+)"/m)?.[1]

assert.ok(read('webui/vite.config.ts').includes("target: 'chrome94'"))
assert.ok(read('webui/src/styles/base.css').includes('.legacy-electron .topbar'))
assert.ok(read('module/webui/app.py').includes("RedirectResponse('/app/'"))

assert.ok(cargoVersion, 'unable to read the desktop version from Cargo.toml')
assert.ok(
  /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$/.test(cargoVersion),
  `Cargo.toml desktop version is not valid semver: ${cargoVersion}`,
)
assert.ok(!('version' in JSON.parse(read('webapp/src-tauri/tauri.conf.json'))), 'tauri.conf.json still declares a top-level version')
assert.ok(!('version' in JSON.parse(read('webapp/package.json'))), 'package.json still declares a version')
assert.ok(!read('deploy/pip.py').includes('nkas_source'), 'legacy root launcher copy remains')
assert.ok(!fs.existsSync(path.join(root, 'deploy/launcher/nkas.bat')), 'legacy desktop launcher remains')
for (const relative of [
  'webapp/buildResources',
  'webapp/packages',
  'webapp/tests',
  'webapp/types',
  'webapp/.env.development',
  'webapp/.yarnclean',
  'webapp/scripts/k2_import_overlay.js',
  'webapp/scripts/shots',
]) {
  assert.ok(!fs.existsSync(path.join(root, relative)), `legacy Electron artifact remains: ${relative}`)
}
const legacyDesktopPath = 'app' + '\\' + 'nkas.exe'
assert.ok(!read('deploy/build/build.bat').includes(legacyDesktopPath), 'local build still creates the legacy desktop path')
assert.ok(!read('.github/workflows/build.yml').includes(legacyDesktopPath), 'CI still creates the legacy desktop path')
assert.ok(read('deploy/build/build.bat').includes('..\\webui\\node_modules'), 'local build does not clean WebUI dependencies')
assert.ok(read('.github/workflows/build.yml').includes('webui\\node_modules'), 'CI does not clean WebUI dependencies')
assert.ok(read('.gitignore').includes('/nkas.exe'), 'root Tauri executable is not ignored')
assert.ok(read('webapp/.gitignore').includes('src-tauri/gen'), 'generated Tauri schemas are not ignored')
assert.ok(read('webapp/shell/index.html').includes('window.nkasStartup'), 'startup log UI is missing')
assert.ok(read('webapp/src-tauri/src/main.rs').includes('WebviewUrl::App("index.html".into())'), 'desktop does not open the startup UI first')

const forbidden = [
  'dialog:' + 'pick-path',
  'nkas-' + 'webui',
  'nkas-' + 'electron',
  '_pick_path_via_' + 'electron',
]
const trackedFiles = childProcess.execFileSync('git', ['ls-files'], { cwd: root, encoding: 'utf8' })
  .split(/\r?\n/)
  .filter(Boolean)
for (const source of trackedFiles) {
  const absolute = path.join(root, source)
  if (!fs.existsSync(absolute)) continue
  const buffer = fs.readFileSync(absolute)
  if (buffer.includes(0)) continue
  const content = buffer.toString('utf8')
  for (const token of forbidden) assert.ok(!content.includes(token), `${token} remains in ${source}`)
}

console.log('desktop compatibility contracts passed')
