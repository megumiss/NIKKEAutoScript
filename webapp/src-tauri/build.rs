fn main() {
    // Declare the app commands in the ACL: the WebUI is served from a remote
    // origin (http://<WebuiHost>:<port>), and Tauri blocks custom commands
    // from remote origins unless a capability explicitly allows them.
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            "desktop_update_status",
            "desktop_update_check",
            "desktop_update_apply",
        ])),
    )
    .expect("failed to run tauri-build");
}
