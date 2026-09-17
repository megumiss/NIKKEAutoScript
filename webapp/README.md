# NKAS desktop shell

The desktop client is a Tauri 2 shell. It starts the existing Python web service
and opens the backend-hosted SPA in the system WebView2 runtime.

The optional security entry is off by default and is enabled on the deployment
page. For a local installation the shell reads `config/.security/entry.key`,
opens the entry automatically and authenticates native exports/shortcuts. After
rotation it rereads the local key; local credentials are not forwarded to a
configured remote host. See [security entry usage](../doc/security-entry.md).

```powershell
yarn install --frozen-lockfile
yarn test
yarn run check
yarn run compile
```

The release executable is written to `src-tauri/target/release/nkas.exe` and is
copied to the project root as `nkas.exe` by the release and local packaging
workflows.

The desktop version in `package.json`, `src-tauri/Cargo.toml`, and
`src-tauri/tauri.conf.json` is independent from the project release tag. Bump it
only when the Tauri shell, its assets, or dependency inputs change.
