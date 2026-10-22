#!/usr/bin/env bash
set -euo pipefail

# Prepare an Ubuntu 22.04/24.04 server for graph database fuzzing experiments.
# Intended usage:
#   bash scripts/bootstrap_ubuntu.sh
#   bash scripts/bootstrap_ubuntu.sh --no-codex
#   bash scripts/bootstrap_ubuntu.sh --no-docker --build-tools-only

INSTALL_DOCKER=1
INSTALL_POWERSHELL=1
INSTALL_CODEX=1
INSTALL_GO=1
BUILD_TOOLS=1
SETUP_SWAP=0
SWAP_SIZE_GB=64
GO_VERSION="1.22.12"

usage() {
  cat <<'EOF'
Usage: bash scripts/bootstrap_ubuntu.sh [options]

Options:
  --no-docker          Skip Docker Engine installation.
  --no-powershell     Skip PowerShell installation.
  --no-codex          Skip Codex CLI installation.
  --no-go             Skip Go installation.
  --no-build          Skip building GDsmith/GDBMeter/Dinkel.
  --build-tools-only  Build tools only; skip system package installation.
  --setup-swap [GB]   Create /swapfile if absent. Default size: 64 GB.
  --go-version VER    Install Go version if needed. Default: 1.22.12.
  -h, --help          Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-docker) INSTALL_DOCKER=0; shift ;;
    --no-powershell) INSTALL_POWERSHELL=0; shift ;;
    --no-codex) INSTALL_CODEX=0; shift ;;
    --no-go) INSTALL_GO=0; shift ;;
    --no-build) BUILD_TOOLS=0; shift ;;
    --build-tools-only)
      INSTALL_DOCKER=0
      INSTALL_POWERSHELL=0
      INSTALL_CODEX=0
      INSTALL_GO=0
      SETUP_SWAP=0
      shift
      ;;
    --setup-swap)
      SETUP_SWAP=1
      if [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]]; then
        SWAP_SIZE_GB="$2"
        shift 2
      else
        shift
      fi
      ;;
    --go-version)
      GO_VERSION="${2:?missing Go version}"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

log() {
  printf '\n[bootstrap] %s\n' "$*"
}

require_ubuntu() {
  if [[ ! -r /etc/os-release ]]; then
    echo "Cannot detect OS: /etc/os-release missing." >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "This script is intended for Ubuntu. Detected ID=${ID:-unknown}." >&2
    exit 1
  fi
  case "${VERSION_ID:-}" in
    22.04|24.04) ;;
    *)
      echo "Warning: tested on Ubuntu 22.04/24.04, detected VERSION_ID=${VERSION_ID:-unknown}." >&2
      ;;
  esac
}

repo_root() {
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  cd "$script_dir/.." && pwd
}

sudo_noninteractive() {
  sudo DEBIAN_FRONTEND=noninteractive "$@"
}

apt_install_base() {
  log "Installing base packages"
  sudo_noninteractive apt-get update
  sudo_noninteractive apt-get install -y \
    ca-certificates curl wget gnupg lsb-release software-properties-common \
    git unzip zip jq tmux htop psmisc procps netcat-openbsd \
    build-essential pkg-config make cmake \
    ninja-build clang llvm lcov \
    python3 python3-pip python3-venv python3-dev \
    openjdk-21-jdk openjdk-17-jdk openjdk-11-jdk \
    maven gradle
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker already installed: $(docker --version)"
  else
    log "Installing Docker Engine from Docker apt repository"
    sudo_noninteractive apt-get update
    sudo_noninteractive apt-get install -y ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    local codename arch
    # shellcheck disable=SC1091
    . /etc/os-release
    codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
    arch="$(dpkg --print-architecture)"
    sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${codename}
Components: stable
Architectures: ${arch}
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    sudo_noninteractive apt-get update
    sudo_noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi

  log "Enabling Docker and adding current user to docker group"
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
  if docker ps >/dev/null 2>&1; then
    log "Docker works for current shell."
  else
    log "Docker installed. Run 'newgrp docker' or reconnect SSH before using docker without sudo."
  fi
}

install_powershell() {
  if command -v pwsh >/dev/null 2>&1; then
    log "PowerShell already installed: $(pwsh -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()')"
    return
  fi

  log "Installing PowerShell from Microsoft package repository"
  # shellcheck disable=SC1091
  . /etc/os-release
  local tmp_deb="/tmp/packages-microsoft-prod.deb"
  wget -q "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/packages-microsoft-prod.deb" -O "$tmp_deb"
  sudo dpkg -i "$tmp_deb"
  rm -f "$tmp_deb"
  sudo_noninteractive apt-get update
  sudo_noninteractive apt-get install -y powershell
}

version_at_least() {
  # Returns true when $1 >= $2 using sort -V.
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

install_go() {
  local installed=""
  if command -v go >/dev/null 2>&1; then
    installed="$(go version | awk '{print $3}' | sed 's/^go//')"
  fi
  if [[ -n "$installed" ]] && version_at_least "$installed" "1.22.0"; then
    log "Go already installed: $(go version)"
    return
  fi

  log "Installing Go ${GO_VERSION}"
  local arch tar_arch url tmp_tgz
  arch="$(dpkg --print-architecture)"
  case "$arch" in
    amd64) tar_arch="amd64" ;;
    arm64) tar_arch="arm64" ;;
    *) echo "Unsupported Go architecture: $arch" >&2; exit 1 ;;
  esac
  url="https://go.dev/dl/go${GO_VERSION}.linux-${tar_arch}.tar.gz"
  tmp_tgz="/tmp/go${GO_VERSION}.linux-${tar_arch}.tar.gz"
  curl -fsSL "$url" -o "$tmp_tgz"
  sudo rm -rf /usr/local/go
  sudo tar -C /usr/local -xzf "$tmp_tgz"
  rm -f "$tmp_tgz"
  if ! grep -q '/usr/local/go/bin' "$HOME/.profile" 2>/dev/null; then
    printf '\nexport PATH=/usr/local/go/bin:$PATH\n' >> "$HOME/.profile"
  fi
  export PATH="/usr/local/go/bin:$PATH"
  log "Installed $(go version)"
}

install_codex() {
  if command -v codex >/dev/null 2>&1; then
    log "Codex CLI already installed: $(codex --version 2>/dev/null || true)"
    return
  fi
  log "Installing Codex CLI"
  curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh
  if ! command -v codex >/dev/null 2>&1; then
    echo "Codex installer finished, but 'codex' is not on PATH yet. Reopen shell or add ~/.local/bin to PATH if needed." >&2
  fi
}

setup_swap() {
  if swapon --show=NAME | grep -qx '/swapfile'; then
    log "/swapfile already enabled"
    return
  fi
  if [[ -e /swapfile ]]; then
    echo "/swapfile exists but is not active. Inspect it manually before enabling swap." >&2
    return
  fi
  log "Creating ${SWAP_SIZE_GB}GB /swapfile"
  sudo fallocate -l "${SWAP_SIZE_GB}G" /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1G count="$SWAP_SIZE_GB" status=progress
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
}

build_gdsmith() {
  local dir="$1/external_tools/GDsmith"
  if [[ ! -d "$dir" ]]; then
    log "GDsmith directory not found; skipping."
    return
  fi
  log "Building GDsmith"
  (
    cd "$dir"
    mvn -q -DskipTests package dependency:copy-dependencies
  )
}

build_gdbmeter() {
  local dir="$1/external_tools/gdbmeter"
  if [[ ! -d "$dir" ]]; then
    log "GDBMeter directory not found; skipping."
    return
  fi
  log "Building GDBMeter"
  (
    cd "$dir"
    if [[ -x ./gradlew ]]; then
      if [[ -f gradle/wrapper/gradle-wrapper.properties ]]; then
        sed -i 's#^distributionUrl=https\\://services.gradle.org/distributions/#distributionUrl=https\\://mirrors.cloud.tencent.com/gradle/#' gradle/wrapper/gradle-wrapper.properties
        if grep -q '^networkTimeout=' gradle/wrapper/gradle-wrapper.properties; then
          sed -i 's/^networkTimeout=.*/networkTimeout=60000/' gradle/wrapper/gradle-wrapper.properties
        else
          printf 'networkTimeout=60000\n' >> gradle/wrapper/gradle-wrapper.properties
        fi
      fi
      ./gradlew --no-daemon clean shadowJar
    else
      gradle --no-daemon clean shadowJar
    fi
  )
}

build_dinkel() {
  local dir="$1/external_tools/dinkel"
  if [[ ! -d "$dir" ]]; then
    log "Dinkel directory not found; skipping."
    return
  fi
  if ! command -v go >/dev/null 2>&1; then
    log "Go is unavailable; skipping Dinkel build."
    return
  fi
  log "Building Dinkel"
  (
    cd "$dir"
    export GOPROXY="${GOPROXY:-https://goproxy.cn,direct}"
    export GOSUMDB="${GOSUMDB:-sum.golang.google.cn}"
    if [[ -f go.mod ]]; then
      go build -o dinkel .
    else
      log "No go.mod in Dinkel directory; skipping."
    fi
  )
}

write_env_report() {
  local root="$1"
  local out_dir="$root/paper/server_setup"
  local out_file="$out_dir/ubuntu_bootstrap_env_$(date +%Y%m%d_%H%M%S).md"
  mkdir -p "$out_dir"
  {
    echo "# Ubuntu Bootstrap Environment"
    echo
    echo "- Date: $(date -Is)"
    echo "- Hostname: $(hostname)"
    echo "- OS: $(. /etc/os-release && echo "$PRETTY_NAME")"
    echo "- Kernel: $(uname -a)"
    echo "- CPU: $(nproc) logical cores"
    echo "- Memory:"
    free -h
    echo
    echo "## Tools"
    echo
    command -v java >/dev/null 2>&1 && java -version 2>&1 || true
    command -v javac >/dev/null 2>&1 && javac -version 2>&1 || true
    command -v docker >/dev/null 2>&1 && docker --version || true
    command -v docker >/dev/null 2>&1 && docker compose version || true
    command -v pwsh >/dev/null 2>&1 && pwsh -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' || true
    command -v mvn >/dev/null 2>&1 && mvn -version || true
    command -v gradle >/dev/null 2>&1 && gradle -version || true
    command -v go >/dev/null 2>&1 && go version || true
    command -v codex >/dev/null 2>&1 && codex --version || true
  } > "$out_file"
  log "Wrote environment report: $out_file"
}

main() {
  require_ubuntu
  local root
  root="$(repo_root)"
  log "Repository root: $root"

  if [[ "$INSTALL_DOCKER" -eq 1 || "$INSTALL_POWERSHELL" -eq 1 || "$INSTALL_GO" -eq 1 || "$INSTALL_CODEX" -eq 1 ]]; then
    apt_install_base
  fi
  if [[ "$INSTALL_DOCKER" -eq 1 ]]; then
    install_docker
  fi
  if [[ "$INSTALL_POWERSHELL" -eq 1 ]]; then
    install_powershell
  fi
  if [[ "$INSTALL_GO" -eq 1 ]]; then
    install_go
  fi
  if [[ "$INSTALL_CODEX" -eq 1 ]]; then
    install_codex
  fi
  if [[ "$SETUP_SWAP" -eq 1 ]]; then
    setup_swap
  fi
  if [[ "$BUILD_TOOLS" -eq 1 ]]; then
    build_gdsmith "$root"
    build_gdbmeter "$root"
    build_dinkel "$root"
  fi
  write_env_report "$root"

  cat <<'EOF'

[bootstrap] Done.

Recommended next steps:
  1. Reconnect SSH or run: newgrp docker
  2. Start a tmux session: tmux new -s graph-exp
  3. In the repo root, run: codex
  4. For existing PowerShell experiment scripts on Ubuntu, use pwsh:
       pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/<script>.ps1

EOF
}

main "$@"
