# Egress reconciliation

Generated from `draupnir/svalinn/egress.py` and the transcription of VLD-INF-SINDRI-001 Rev 3.3 section 10.5 Egress policy held in `draupnir/svalinn/site_egress.py`. Do not edit by hand.

The transcription was taken on 2026-09-08. Nothing in the pipeline can verify it against the controlled document, which is why re-checking it is a step in the acceptance schedule rather than a test.

## Blocked by the site router

Something in the programme reaches these and section 10.5 does not permit them. The packet leaves the host and is dropped, so the failure presents as a timeout rather than as a policy decision.

| Host | Needed by | Why | Where the call is written |
|---|---|---|---|
| `astral.sh` | commissioning | the uv installer, on every appliance and on both Macs | VLD-INF-SINDRI-001 Procedure S6 step 1: curl -LsSf https://astral.sh/uv/install.sh |
| `cgr.dev` | image-build | the Chainguard node and nginx images (docker/web.Dockerfile) | draupnir/svalinn/egress.py ALLOW_LIST |
| `download.pytorch.org` | commissioning | the PyTorch wheels -- the training stack itself | VLD-INF-SINDRI-001 Procedure S6 step 2: uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130 |
| `gcr.io` | image-build | the distroless runtime image (docker/api.Dockerfile) | draupnir/svalinn/egress.py ALLOW_LIST |
| `ghcr.io` | image-build | the uv builder image (docker/api.Dockerfile) | draupnir/svalinn/egress.py ALLOW_LIST |
| `nvidia.github.io` | commissioning | the NVIDIA container toolkit signing key and apt source | VLD-INF-SINDRI-001 Procedure S5: curl https://nvidia.github.io/libnvidia-container/gpgkey |
| `pkg-containers.githubusercontent.com` | image-build | the layers themselves, where ghcr.io serves them from | draupnir/svalinn/egress.py ALLOW_LIST |
| `storage.googleapis.com` | image-build | the layers themselves, where gcr.io serves them from | draupnir/svalinn/egress.py ALLOW_LIST |
| `www.legislation.gov.uk` | corpus | GBR primary legislation, under the Open Government Licence v3.0 | VLD-INF-SINDRI-001 Part 5, the GBR corpus manifest |

## Every host, and where the two policies stand

| Host | Verdict | Needed by | Why |
|---|---|---|---|
| `astral.sh` | blocked-by-the-router | commissioning | the uv installer, on every appliance and on both Macs |
| `cgr.dev` | blocked-by-the-router | image-build | the Chainguard node and nginx images (docker/web.Dockerfile) |
| `download.pytorch.org` | blocked-by-the-router | commissioning | the PyTorch wheels -- the training stack itself |
| `gcr.io` | blocked-by-the-router | image-build | the distroless runtime image (docker/api.Dockerfile) |
| `ghcr.io` | blocked-by-the-router | image-build | the uv builder image (docker/api.Dockerfile) |
| `nvidia.github.io` | blocked-by-the-router | commissioning | the NVIDIA container toolkit signing key and apt source |
| `pkg-containers.githubusercontent.com` | blocked-by-the-router | image-build | the layers themselves, where ghcr.io serves them from |
| `storage.googleapis.com` | blocked-by-the-router | image-build | the layers themselves, where gcr.io serves them from |
| `www.legislation.gov.uk` | blocked-by-the-router | corpus | GBR primary legislation, under the Open Government Licence v3.0 |
| `megingjord.veldris.internal` | internal-to-the-site | control-plane | chain-head anchoring, policy pull, release metadata push, the JWKS this API verifies bearer tokens against, the authorisation-code exchange the console signs in through, and the readiness probe that reports whether the wide-area link is up |
| `regin.sindri.veldris.internal` | internal-to-the-site | control-plane | job submission, status and cancellation over slurmrestd, and the readiness probe that reports whether the scheduler is answering; reading appliance thermal, throttle and fabric measurements |
| `api.github.com` | permitted-but-unclaimed | — | nothing in this programme is recorded as reaching it |
| `api.ngc.nvidia.com` | permitted-but-unclaimed | — | nothing in this programme is recorded as reaching it |
| `auth.docker.io` | permitted-but-unclaimed | — | nothing in this programme is recorded as reaching it |
| `codeload.github.com` | permitted-but-unclaimed | — | nothing in this programme is recorded as reaching it |
| `developer.download.nvidia.com` | permitted-but-unclaimed | — | nothing in this programme is recorded as reaching it |
| `nvcr.io` | permitted-but-unclaimed | — | nothing in this programme is recorded as reaching it |
| `production.cloudflare.docker.com` | permitted-but-unclaimed | — | nothing in this programme is recorded as reaching it |
| `registry-1.docker.io` | permitted-but-unclaimed | — | nothing in this programme is recorded as reaching it |
| `archive.ubuntu.com` | reconciled | commissioning | DGX OS package updates on the three appliances |
| `cdn-lfs.huggingface.co` | reconciled | control-plane | the weight blobs themselves, where huggingface.co redirects them |
| `files.pythonhosted.org` | reconciled | image-build | the wheels themselves, where pypi.org serves them from |
| `github.com` | reconciled | commissioning | LLaMA-Factory and the other training tools, cloned at commissioning |
| `huggingface.co` | reconciled | control-plane | base model and tokeniser acquisition, pinned by revision |
| `ports.ubuntu.com` | reconciled | commissioning | the aarch64 package archive, which is where a DGX Spark's apt resolves |
| `pypi.org` | reconciled | image-build | dependency resolution at image build time |
| `raw.githubusercontent.com` | reconciled | commissioning | the Homebrew installer, on ALVISS and ANDVARI |
| `registry.npmjs.org` | reconciled | image-build | the console's dependency resolution at image build time |
| `security.ubuntu.com` | reconciled | commissioning | DGX OS security updates on the three appliances |

## Declared, but not reachable yet

A permission for a path that does not exist. Worth having declared, and worth not mistaking for a live one.

- `megingjord.veldris.internal` — the WireGuard link to Veldris_NXT is not built, so this name does not resolve and this permission is for a path that does not exist yet. It terminates on REGIN rather than here (RF-E21): REGIN already carries wireguard-tools, it is the machine on both fabrics, and it returns from a power cut without anybody typing a FileVault password at a console. So the control plane reaches MEGINGJORD through a route, and what has to exist is a dnsmasq record on REGIN and a route on this host -- neither of which changes anything the broker decides.

## Deliberately absent

`api.teacher-model.example` is not in either list and must not be. Distillation is out of scope for Release 1 (SAD Q3) and threat T3 is distillation-time exfiltration of corpus content. AC-S3 requires the destination to be absent and a call to it to fail with a logged refusal.

