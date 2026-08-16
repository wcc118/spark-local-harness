FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates coreutils git jq python3 python3-pytest ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /workspace /worker-home
WORKDIR /workspace

