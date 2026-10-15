# Reproduce and develop

The offline demonstration and regression suite need no API key. Python 3.12 or newer is required for local development. CI covers Python 3.12, 3.13, and 3.14 on Linux. Docker uses Python 3.12.12 on Debian Bookworm; macOS local development uses the same dependency locks.

## One-command Docker paths

Install Docker with the Compose v2 plugin, then run from the repository root:

```sh
make demo
make test
make benchmark RUNS=30
```

Each target builds its image as needed and removes its execution container on exit. Dependency installation requires internet during the image build. The offline execution service has no network, receives no provider credentials, runs with your uid/gid, and writes generated evidence under `artifacts/`. Its root filesystem is read-only; a small temporary filesystem and the artifact directory are writable. Resource limits bound the container. These settings help make the demo predictable; this project does not claim to safely execute arbitrary untrusted code.

`RUNNER_UID` and `RUNNER_GID` default to the current user's values through Make. Override them when required by your Docker environment. When invoking Compose directly, create `artifacts/` first and pass the uid/gid explicitly if you need host ownership to match.

## Local paths

No global Python packages are installed:

```sh
make local
make local-benchmark RUNS=30
```

`make local` creates `.venv`, installs the hash-checked development lock, installs this project in editable mode without resolving new dependencies, then runs the demo and tests. Select the interpreter for a fresh environment with `make setup PYTHON=python3.13`; an existing environment retains its original interpreter. Select another environment with `VENV=/path/to/venv`.

Individual commands:

```sh
make setup
make local-demo
make local-test
make lint
make typecheck
make build
.venv/bin/python -m pip check
```

Local demo and test commands are deterministic provider fixtures, but the host environment is not network-isolated. Report machine and interpreter details alongside measurements. A fresh image/environment still needs PyPI access to install the pinned distributions.

## Explicit live execution

Choose a model and export your own credentials before using the live path:

```sh
export OPENAI_API_KEY='your key'
export OPENAI_MODEL='your chosen model ID'
make live
```

The live service can reach the network. `make live` rejects missing configuration before starting the container. To run locally, use `.venv/bin/python -m tool_loop live --model "$OPENAI_MODEL" --output artifacts/live`. This may make billable provider requests. Read the README for the workload and budget before running it. The environment example contains variable names only; secrets and generated artifacts are ignored by Git and excluded from the image build context. `.env` is not automatically loaded by the application or local Make targets.

## Dependency discipline

`pyproject.toml` pins the direct runtime dependencies. `requirements.lock` pins every transitive runtime dependency; `requirements-dev.lock` includes that runtime lock plus every development dependency. `requirements-build.lock` pins the build backend and its support libraries. Each package entry includes the SHA-256 hashes published by PyPI for that exact release, permitting compatible wheels across the supported Python versions and platforms. Installation uses `--only-binary=:all: --require-hashes` so it cannot silently build third-party source packages with unpinned build dependencies; editable installation uses `--no-deps --no-build-isolation` after the locked backend has been installed. Platforms without compatible wheels fail explicitly.

The OpenAI adapter calls the raw SDK. The loop, concurrency policy, validation, and recovery logic belong to this repository. OpenTelemetry is explicitly instrumented through its SDK; no orchestration framework or global auto-instrumentation is installed. The default span exporter writes local evidence, so there is no telemetry collector requirement.

To update dependencies, resolve direct version changes in a fresh temporary environment for each supported Python version, inspect the dependency diff and release notes, regenerate the full transitive pins and PyPI distribution hashes, and change the direct pins in `pyproject.toml` together. Never remove hashes to make an unexpected resolution pass. Then run:

```sh
make setup lint typecheck local-test local-demo local-benchmark build RUNS=3
.venv/bin/python -m pip check
make test demo
```

Review lock changes as code. The Docker base is pinned to the official Python multi-platform index digest as well as its interpreter/OS tag; update that digest deliberately when taking interpreter or OS fixes. CI installs and runs the locks under all supported Python versions, builds both image stages, and runs the documented Docker paths without network access during execution. A passing local run does not establish that a remote CI run has passed.
