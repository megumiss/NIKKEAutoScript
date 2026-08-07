# NKAS desktop shell

The desktop client is a Tauri 2 shell. It starts the existing Python web service
and opens the backend-hosted SPA in the system WebView2 runtime.

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
