#!/data/data/com.termux/files/usr/bin/bash
set -u

STATE_DIR="${HOME}/.nkas"
STATE_FILE="${STATE_DIR}/state"
LOG_FILE="${STATE_DIR}/bootstrap.log"
CONTAINER_MARKER="${STATE_DIR}/container-image"
REPO_DIR="${HOME}/NIKKEAutoScript"
if [ -f "$STATE_DIR/settings.env" ]; then
    . "$STATE_DIR/settings.env"
fi
APT_SOURCE="${NKAS_APT_SOURCE:-https://mirrors.tuna.tsinghua.edu.cn/termux/apt/termux-main}"
DOCKER_IMAGE="${NKAS_DOCKER_IMAGE:-docker.1ms.run/megumiss/nkas:latest}"
REPOSITORY="${NKAS_REPOSITORY:-https://git.megumiss.top/megumiss/NIKKEAutoScript}"
BRANCH="${NKAS_BRANCH:-master}"

mkdir -p "$STATE_DIR"

LOCK_DIR="${STATE_DIR}/bootstrap.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    existing_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
        printf '[nkas] bootstrap already running (PID %s)\n' "$existing_pid"
        exit 2
    fi
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" || exit 2
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT

# Keep the visible log scoped to this run so retries cannot mix old stages into the current one.
: > "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

set_state() {
    printf '%s\n' "$1" > "$STATE_FILE"
    printf '[nkas] state=%s\n' "$1"
}

fail() {
    printf '[nkas] ERROR: %s\n' "$1"
    set_state failed
    exit 1
}

run_stage() {
    local state="$1"
    shift
    set_state "$state"
    "$@" || fail "stage ${state} failed (exit=$?)"
}

prepare_tools() {
    export DEBIAN_FRONTEND=noninteractive
    case "$APT_SOURCE" in
        https://*) printf 'deb %s stable main\n' "$APT_SOURCE" > "${PREFIX}/etc/apt/sources.list" ;;
        *) return 1 ;;
    esac
    dpkg --force-confold --configure -a
    apt-get -o Acquire::Retries=3 update -y
    apt-get -o Acquire::Retries=3 -y -o Dpkg::Options::=--force-confold upgrade
    apt-get -o Acquire::Retries=3 --fix-missing -y -o Dpkg::Options::=--force-confold install git proot-distro android-tools curl
}

sync_repository() {
    if [ -d "$REPO_DIR/.git" ]; then
        git -C "$REPO_DIR" fetch --depth=1 origin "$BRANCH"
        git -C "$REPO_DIR" reset --hard "origin/$BRANCH"
    else
        rm -rf "$REPO_DIR.tmp"
        git clone --depth=1 --branch "$BRANCH" "$REPOSITORY" "$REPO_DIR.tmp"
        mv "$REPO_DIR.tmp" "$REPO_DIR"
    fi
}

create_config() {
    cd "$REPO_DIR" || return 1
    if [ ! -f config/nkas.json ]; then
        cp config/template.json config/nkas.json || return 1
    fi
    sed -i -E 's/"Platform":[[:space:]]*"win"/"Platform": "adb"/' config/nkas.json
    sed -i -E 's/"OcrThreads"[[:space:]]*:[[:space:]]*[0-9]+/"OcrThreads": 1/' config/nkas.json
    sed -i -E 's/"ScreenshotMethod":[[:space:]]*"DroidCast"/"ScreenshotMethod": "ADB"/' config/nkas.json
    sed -i -E 's/("ControlMethod"[[:space:]]*:[[:space:]]*)"[^"]*"/\1"MaaTouch"/' config/nkas.json
    sed -i -E '/"PhysicalDevice"[[:space:]]*:[[:space:]]*\{/,/^[[:space:]]*\},?[[:space:]]*$/ s/"Enable":[[:space:]]*false/"Enable": true/' config/nkas.json
    sed -i -E '/"PhysicalDevice"[[:space:]]*:[[:space:]]*\{/,/^[[:space:]]*\},?[[:space:]]*$/ s/("VirtualDisplay"[[:space:]]*:[[:space:]]*)(true|false)/\1true/' config/nkas.json
    local serial
    serial="$(detect_wireless_serial || true)"
    if [ -n "$serial" ]; then
        sed -i -E "s/(\"Serial\"[[:space:]]*:[[:space:]]*)\"[^\"]*\"/\1\"$serial\"/" config/nkas.json
        printf '[nkas] detected wireless serial: %s\n' "$serial"
    else
        printf '[nkas] wireless serial was not detected; keeping existing value\n'
    fi
    if [ ! -f config/deploy.yaml ]; then
        cp config/deploy.template-docker-cn.yaml config/deploy.yaml || return 1
    fi
    sed -i -E 's/^([[:space:]]+WebuiHost:).*/\1 127.0.0.1/' config/deploy.yaml
    sed -i -E 's/^([[:space:]]+WebuiPort:).*/\1 12271/' config/deploy.yaml
    sed -i -E '/^Client:/,/^Emulator:/ s/^(    value:) win$/\1 adb/' config/deploy.yaml
    sed -i -E '/^Emulator:/,/^PhysicalDevice:/ s/^(    value:) DroidCast$/\1 ADB/' config/deploy.yaml
    sed -i -E '/^Emulator:/,/^PhysicalDevice:/ s/^(    value:) minitouch$/\1 MaaTouch/' config/deploy.yaml
    sed -i -E '/^PhysicalDevice:/,/^Scrcpy:/ s/^(    value:) false$/\1 true/' config/deploy.yaml
}

tools_ready() {
    command -v git >/dev/null 2>&1 && command -v proot-distro >/dev/null 2>&1 && \
        command -v curl >/dev/null 2>&1 && command -v adb >/dev/null 2>&1
}

source_ready() {
    [ -d "$REPO_DIR/.git" ] && git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

detect_wireless_serial() {
    local local_ip wireless_port candidate serial
    local_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)"
    if [ -z "$local_ip" ]; then
        local_ip="$(ip -4 addr show scope global 2>/dev/null | sed -n 's/.* inet \([0-9.]*\)\/.*/\1/p' | head -n1)"
    fi
    if [ -z "$local_ip" ]; then
        local_ip="$(getprop dhcp.wlan0.ipaddress 2>/dev/null | tr -d '\r' | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -n1)"
    fi

    wireless_port="$(settings get global adb_wifi_port 2>/dev/null | tr -d '\r' | grep -E '^[0-9]+$' | head -n1)"
    if [ -z "$wireless_port" ]; then
        wireless_port="$(getprop persist.adb.tcp.port 2>/dev/null | tr -d '\r' | grep -E '^[0-9]+$' | head -n1)"
    fi

    if [ -n "$local_ip" ] && [ -n "$wireless_port" ]; then
        candidate="${local_ip}:${wireless_port}"
        adb connect "$candidate" >/dev/null 2>&1 || true
        if adb -s "$candidate" get-state >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi

    if [ -n "$local_ip" ]; then
        # `adb mdns services` inserts a count column when multiple services
        # share the same name, so the service type/address may be in columns
        # 2/3 or 3/4 depending on the daemon output.
        for candidate in $(adb mdns services 2>/dev/null | awk -v ip="$local_ip" '
            ($2 == "_adb-tls-connect._tcp" && index($3, ip ":") == 1) { print $3 }
            ($3 == "_adb-tls-connect._tcp" && index($4, ip ":") == 1) { print $4 }
        '); do
            adb connect "$candidate" >/dev/null 2>&1 || true
            if adb -s "$candidate" get-state >/dev/null 2>&1; then
                printf '%s\n' "$candidate"
                return 0
            fi
        done
    fi

    serial="$(adb devices 2>/dev/null | awk '$2 == "device" && $1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+$/ { print $1; exit }')"
    [ -n "$serial" ] && printf '%s\n' "$serial"
}

config_ready() {
    [ -f "$REPO_DIR/config/nkas.json" ]
}

container_ready() {
    [ -d "${PREFIX}/var/lib/proot-distro/containers/nkas/rootfs" ] && \
        [ -f "$CONTAINER_MARKER" ] && [ "$(cat "$CONTAINER_MARKER")" = "$DOCKER_IMAGE" ] && \
        proot-distro run -b "$REPO_DIR:/app/NIKKEAutoScript" nkas -- /usr/local/bin/python -c 'import uvicorn' >/dev/null 2>&1
}

service_ready() {
    curl -fsS --max-time 3 http://127.0.0.1:12271/api/system/status >/dev/null 2>&1
}

install_container() {
    if [ -d "${PREFIX}/var/lib/proot-distro/containers/nkas/rootfs" ] && [ -f "$CONTAINER_MARKER" ] && [ "$(cat "$CONTAINER_MARKER")" = "$DOCKER_IMAGE" ] && \
        proot-distro run -b "$REPO_DIR:/app/NIKKEAutoScript" nkas -- /usr/local/bin/python -c 'import uvicorn' >/dev/null 2>&1; then
        return 0
    fi
    if [ -d "${PREFIX}/var/lib/proot-distro/containers/nkas/rootfs" ]; then
        proot-distro remove nkas || return 1
    fi
    case "$DOCKER_IMAGE" in
        *[!A-Za-z0-9._:/-]*|'') return 1 ;;
    esac
    proot-distro install "$DOCKER_IMAGE" --name nkas || return 1
    printf '%s\n' "$DOCKER_IMAGE" > "$CONTAINER_MARKER"
}

start_service() {
    bash "$STATE_DIR/nkas-service.sh" start || return 1
    local attempt
    for attempt in $(seq 1 30); do
        if bash "$STATE_DIR/nkas-service.sh" status; then
            printf '[nkas] Web UI is healthy (attempt=%s)\n' "$attempt"
            return 0
        fi
        printf '[nkas] waiting for Web UI (attempt=%s/30)\n' "$attempt"
        sleep 2
    done
    printf '[nkas] Web UI health check timed out\n' >&2
    return 1
}

if ! tools_ready; then
    run_stage installing-termux-tools prepare_tools
fi
if ! source_ready; then
    run_stage cloning-nkas sync_repository
fi
run_stage creating-config create_config
if service_ready && config_ready; then
    set_state ready
    exit 0
fi
if ! container_ready; then
    run_stage installing-container install_container
fi
run_stage starting-nkas start_service

set_state ready
printf '[nkas] bootstrap complete\n'
