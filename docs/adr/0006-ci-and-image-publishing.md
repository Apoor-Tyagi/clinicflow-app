# ADR 0006: CI and image publishing

Status: Accepted
Date: 2026-11-10

## Context

Container images should be tested and scanned before they reach a registry, and
only reviewed code on the main branch should be able to publish.

## Decision

- A workflow with three jobs: test (pytest), build-scan (build, then Trivy), and
  push (multi-architecture build and push to ECR).
- Pull requests run test and build-scan only. The push job runs only for a push
  to main, enforced twice: by the workflow condition and by the role trust policy.
- A separate role (clinicflow-github-ecr-push) can push to the clinicflow-api
  repository only. The read-only plan role is a different role.
- Trivy is pinned to the version used locally. It fails the build on fixable HIGH
  or CRITICAL findings and ignores findings with no upstream fix.
- Images are built for linux/amd64 and linux/arm64. A Mac builds arm64, and the
  EC2 node is x86_64.
- ECR tags are immutable and each image is tagged with the commit SHA, never latest.
- ECR scans on push, uses AES256, and keeps the 20 most recent images to stay
  within the free storage allowance.

## Alternatives considered

- Pushing from pull requests: faster feedback, but an unreviewed branch could publish.
- A mutable latest tag: convenient, but the scanned image may not be what runs.

## Consequences

- Re-running the push for the same commit fails, because the tag already exists.
  A new commit produces a new tag.
- The arm64 half of the build runs under emulation and is slower.
- The first push failed because the trust policy still held a placeholder instead
  of the repository IDs. Stricter variable validation now catches that at plan time.

## Finding: retention counts entries, not pushes

Each multi-architecture push creates three ECR entries: a tagged index and one
untagged image per platform (about 72 MB each). A rule that keeps 20 entries keeps
about 7 pushes. After about 7 pushes, the image pinned in the AWS overlay had
expired, which would have left the API pods in ImagePullBackOff on the next node.
Retention was raised to 60 entries (about 20 pushes) and the overlay was repinned
to an image that exists. Protecting the deployed tag, or automating the tag bump,
is the durable fix.
