#!/data/data/com.termux/files/usr/bin/bash
set -u

STATE_DIR="${HOME}/.nkas"
STATE_FILE="${STATE_DIR}/state"
LOG_FILE="${STATE_DIR}/bootstrap.log"
REPO_DIR="${HOME}/NIKKEAutoScript"
REPOSITORY="${NKAS_REPOSITORY:-https://git.megumiss.top/megumiss/NIKKEAutoScript}"
BRANCH="${NKAS_BRANCH:-master}"

mkdir -p "$STATE_DIR"
touch "$LOG_FILE"
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
    sed -i -E 's/^(  WebuiHost:).*/\1 127.0.0.1/' config/deploy.yaml
    sed -i -E 's/^(  WebuiPort:).*/\1 12271/' config/deploy.yaml
    sed -i -E '/^Client:/,/^Emulator:/ s/^(    value:) win$/\1 adb/' config/deploy.yaml
    sed -i -E '/^Emulator:/,/^PhysicalDevice:/ s/^(    value:) DroidCast$/\1 ADB/' config/deploy.yaml
    sed -i -E '/^Emulator:/,/^PhysicalDevice:/ s/^(    value:) minitouch$/\1 MaaTouch/' config/deploy.yaml
    sed -i -E '/^PhysicalDevice:/,/^Scrcpy:/ s/^(    value:) false$/\1 true/' config/deploy.yaml
}

install_container() {
    if [ ! -d "${PREFIX}/var/lib/proot-distro/containers/nkas/rootfs" ]; then
        proot-distro install megumiss/nkas:latest --name nkas
    fi
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
