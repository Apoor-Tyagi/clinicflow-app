git checkout -b docs/adr-0005
mkdir -p docs/adr
cat > docs/adr/0005-container-build-strategy.md << 'EOF'
# ADR 0005: Container build strategy

Status: Accepted
Date: 2026-11-10

## Context

The API must run the same way on a laptop, in CI, and on the Kubernetes node.
The image will hold a service that touches patient-related data, so it should
have a small attack surface and be reproducible.

## Decision

- Multi-stage Dockerfile: the builder installs dependencies, only the finished
  virtual environment reaches the runtime image.
- python:3.15-slim base image.
- Runs as a non-root user with a fixed numeric UID (10001), so Kubernetes can
  verify runAsNonRoot.
- Runtime dependencies are pinned in requirements.txt. Test tools live in
  requirements-dev.txt and never enter the image.
- pip, setuptools and wheel are removed from the runtime image.
- apt-get upgrade runs in the runtime stage to pick up published fixes.
- The image has a HEALTHCHECK, and the app exposes /health, /ready, /metrics.

## Alternatives considered

- Full python image: larger, more packages to patch.
- Alpine: smaller, but musl libc causes wheel and compatibility problems.
- Distroless: fewer packages and findings, but harder to debug. This is the
  production step up.
- Single-stage build: simpler, but ships build tools and caches.

## Consequences

- Trivy policy: fail the build on fixable HIGH or CRITICAL findings, and
  document findings with no upstream fix.
- apt-get upgrade makes builds less reproducible, because the result depends on
  the build date. Mitigation: rebuild on a schedule against a fresh base image.
- Python 3.15 is very new, and some packages have no prebuilt wheels yet
  (PyYAML compiled from source). Builds were slow and a pip timeout appeared
  on a slow connection. A production image would normally stay one minor
  version behind the newest.
EOF

ls -la docs/adr
git status