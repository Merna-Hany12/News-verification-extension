#!/bin/bash
# ─── HAQQ Backend — EC2 Setup Script ─────────────────────────────────────────
# Run this after SSH-ing into your fresh EC2 g4dn.xlarge instance
# Usage: chmod +x setup.sh && sudo ./setup.sh

set -e

echo "══════════════════════════════════════════════"
echo "  HAQQ Backend — EC2 Setup"
echo "══════════════════════════════════════════════"

# ─── 1. System updates ───────────────────────────────────────────────────────
echo "▶ Updating system..."
apt-get update && apt-get upgrade -y

# ─── 2. Install Docker ───────────────────────────────────────────────────────
echo "▶ Installing Docker..."
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# ─── 3. Install NVIDIA Container Toolkit ─────────────────────────────────────
echo "▶ Installing NVIDIA Container Toolkit..."
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

# ─── 4. Verify GPU access ────────────────────────────────────────────────────
echo "▶ Verifying GPU access in Docker..."
docker run --rm --gpus all nvidia/cuda:12.1.0-runtime-ubuntu22.04 nvidia-smi

echo ""
echo "══════════════════════════════════════════════"
echo "  ✅ Setup complete!"
echo "  Next steps:"
echo "    1. Clone your repo:  git clone <your-repo-url>"
echo "    2. cd News-verification-extension"
echo "    3. Create backend/.env with your API keys"
echo "    4. docker compose up -d --build"
echo "══════════════════════════════════════════════"
