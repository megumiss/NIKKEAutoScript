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
    if [ ! -f config/deploy.yaml ]; then
        cp config/deploy.template-docker-cn.yaml config/deploy.yaml
    fi
    sed -i -E 's/^([[:space:]]+WebuiHost:).*/\1 127.0.0.1/' config/deploy.yaml
    sed -i -E 's/^([[:space:]]+WebuiPort:).*/\1 12271/' config/deploy.yaml
    sed -i -E '/^Client:/,/^Emulator:/ s/^(    value:) win$/\1 adb/' config/deploy.yaml
    sed -i -E '/^Emulator:/,/^PhysicalDevice:/ s/^(    value:) DroidCast$/\1 ADB/' config/deploy.yaml
    sed -i -E '/^Emulator:/,/^PhysicalDevice:/ s/^(    value:) minitouch$/\1 MaaTouch/' config/deploy.yaml
    sed -i -E '/^PhysicalDevice:/,/^Scrcpy:/ s/^(    value:) false$/\1 true/' config/deploy.yaml
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

current_state="$(cat "$STATE_FILE" 2>/dev/null || true)"
if [ "$current_state" = "ready" ]; then
    bash "$STATE_DIR/nkas-service.sh" status && exit 0 || true
fi

run_stage installing-termux-tools prepare_tools
run_stage cloning-nkas sync_repository
run_stage creating-config create_config
run_stage installing-container install_container
run_stage starting-nkas start_service

set_state ready
printf '[nkas] bootstrap complete\n'
