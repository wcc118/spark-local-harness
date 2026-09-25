FROM node:22-bookworm-slim

# Native build toolchain and runtime libraries let workers compile and run
# repository-built engines (e.g. Fortran codes linked against libgfortran/X11).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates coreutils git jq python3 python3-pip python3-pytest ripgrep \
       build-essential gfortran libgfortran5 libx11-6 libx11-dev \
    && rm -rf /var/lib/apt/lists/*

# Debian bookworm ships NumPy 1.24; scientific repositories require NumPy 2.
RUN python3 -m pip install --no-cache-dir --break-system-packages \
       "numpy>=2,<3" "matplotlib>=3.10,<3.12" \
    && python3 -c "import numpy, matplotlib; assert int(numpy.__version__.split('.')[0]) >= 2"

RUN npm install --global @qwen-code/qwen-code@0.22.0 \
    && qwen --version

RUN mkdir -p /workspace /worker-home
WORKDIR /workspace
