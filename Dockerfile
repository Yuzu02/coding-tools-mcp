FROM rust:1-bookworm AS rust-base

FROM golang:1-bookworm AS go-base

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODING_TOOLS_MCP_WORKSPACE=/workspace \
    CODING_TOOLS_MCP_HOST=0.0.0.0 \
    CODING_TOOLS_MCP_PORT=8765 \
    CODING_TOOLS_MCP_PERMISSION_MODE=trusted \
    CODING_TOOLS_MCP_SHELL_ENV_SET='{"CARGO_HOME":"/usr/local/cargo","RUSTUP_HOME":"/usr/local/rustup"}'

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        clang \
        cmake \
        curl \
        g++ \
        gcc \
        git \
        make \
        maven \
        ninja-build \
        nodejs \
        npm \
        openjdk-17-jdk-headless \
        pkg-config \
        unzip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=go-base /usr/local/go /usr/local/go
COPY --from=rust-base /usr/local/cargo /usr/local/cargo
COPY --from=rust-base /usr/local/rustup /usr/local/rustup

ENV PATH=/usr/local/go/bin:/usr/local/cargo/bin:$PATH \
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY coding_tools_mcp ./coding_tools_mcp
RUN python -m pip install --no-cache-dir .

COPY scripts/docker-entrypoint.sh /usr/local/bin/coding-tools-mcp-docker-entrypoint
RUN chmod +x /usr/local/bin/coding-tools-mcp-docker-entrypoint \
    && mkdir -p /workspace

EXPOSE 8765
ENTRYPOINT ["coding-tools-mcp-docker-entrypoint"]
