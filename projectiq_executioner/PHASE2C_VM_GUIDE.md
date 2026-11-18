# ProjectIQ Executioner — Phase 2C: Disposable Linux VM worker pilot

## What changed

Phase 2C no longer depends on Docker Desktop, Docker daemon contexts, rootless Docker, or Podman. It uses **QEMU system virtualization** with an operator-pinned Linux QCOW2 template, one fresh copy-on-write overlay per job, SSH-only control through `127.0.0.1`, and a native application process inside the guest. The VM contains the database and web server when appropriate. The **Phase 2B Docker runtime probe is legacy information**, deliberately ignored by Phase 2C when assessing the VM path. Keep the original Phase 1/2A/2B JSON as reproducible inputs.

The current implementation is a **reviewed-fixture development pilot**, not yet an approved production environment for arbitrary hostile repositories. It cannot guarantee total isolation; production needs independent adversarial testing and a dedicated worker host.

## The working model

```
Windows / ProjectIQ control plane (NO student process execution)
  Phase 1 JSON + Phase 2A JSON + Phase 2B JSON
              |
              v
      VM-only 2C candidate compiler
              |
         operator-approved repo@SHA
              |
   pinned trusted Ubuntu image (SHA-256 verified)
              |
      per-job qcow2 overlay (destroy after run)
              |
 QEMU WHPX (Windows) / KVM (Linux), restricted user networking
             |             \
   SSH localhost-forward     NO internet egress; NO host folder mounts
             |
  [Guest: trusted controller agent]
        |                      |
     FSDL                   Mini Task Collaboration
  Python static server      PHP internal web server + MySQL localhost
        |                      |
   GET /index.html          SQL import in disposable DB; GET /public/login.html
         \                    /
         readiness JSON, shutdown, remove VM and overlay
```

### Built-in adapters

* `static_html_v1` — a single frontend of `static_web` type with `index.html`, no database or external dependencies. Uses Python's standard-library HTTP server inside the guest (no npm, Python packages, or Docker).
* `php_mysql_v1` — a native PHP backend at repository root, optional cohosted static frontend, one MySQL/MariaDB service, and one `.sql` dump. Uses guest PHP and MySQL 8 already present in the image. The trusted guest agent generates temporary per-run credentials; reviews SQL for known unsupported/dangerous commands; imports into **projectiq_run** only using a database-scoped user; replaces a recognized 4-variable PHP PDO configuration file **inside the VM copy** with environment variable lookups. No edits to the student's GitHub repo or Windows project folder. The underlying SQL review is conservative and heuristic, not a full SQL security proof. A difficult SQL dump may still need an operator-approved adapter.
* Other architectures (Next.js + external backend, FastAPI + S3) deliberately return `NEEDS_ADAPTER` for the pilot. They can be supported by additional guest adapters and offline dependency doubles; *not* by relaxing worker isolation.

For PHP/MySQL, ensure the golden guest image has **MySQL 8.x**, `php-cli`, and `php-mysql`. Old MariaDB versions may reject MySQL 8/9 dumps containing `utf8mb4_0900_ai_ci`.

## Setup on Windows 11

The **Docker Desktop installation can be closed**; it is unused by 2C. You do not need to uninstall Docker if other work uses it.

**One-time operator prerequisites (not performed by Phase 2C):**

1. Install a reputable Windows QEMU build with WHPX support and ensure `qemu-system-x86_64.exe` and `qemu-img.exe` are in `PATH`, or provide their exact paths in config. Enable the Windows optional feature **Windows Hypervisor Platform** and hardware virtualization. WSL2 being installed does *not* by itself prove that QEMU WHPX acceleration works. Run `qemu-system-x86_64 --version` and `qemu-img --version`. If your official cloud image requires UEFI, provide a trusted QEMU/OVMF firmware ROM via `--uefi-firmware` / `--uefi-sha256` when baking and `uefi_firmware` / `uefi_sha256` in the runtime JSON; the firmware is SHA-pinned. Some QEMU builds can boot Ubuntu cloud images with BIOS, others need UEFI.
2. Ensure the Windows OpenSSH client utilities `ssh` and `ssh-keygen` work. These are the **only** remote control tools; no Docker socket or host mounts are used.
3. Install the **trusted controller dependency** `pycdlib` into your ProjectIQ Python environment (`python -m pip install pycdlib`). It creates an ISO9660 cloud-init NoCloud configuration image containing a **per-run generated SSH public key**. This is not a student project's dependency.
4. Download an **official Ubuntu 24.04 x86-64 cloud QCOW2 image** from the [Ubuntu released cloud-image directory](https://cloud-images.ubuntu.com/releases/24.04/release/) and find its *matching* `SHA256SUMS` entry. The `ubuntu-24.04-server-cloudimg-amd64.img` file is a QCOW2 disk despite its `.img` extension. The sha must come from the official manifest, not just a hash calculated from the file you downloaded.
5. Run the included **one-time trusted image baker**, before any student snapshot is introduced. The baker boots the supplied SHA-verified Ubuntu image with temporary setup-only Internet access, installs approved Ubuntu packages (OpenSSH, Python, PHP CLI + PDO MySQL, MySQL server), schedules a root-level sealing job to remove the temporary privileged bootstrap account and cloud-init state, powers off, flattens the QCOW2 overlay into a new template, then **reboots a fresh throwaway copy with outgoing networking blocked** and checks PHP/MySQL and that unrestricted sudo is denied. This is useful *operator setup*, not student execution and not security attestation. The builder and WHPX path still require their first real Windows smoke test.
6. Preserve the golden disk **read-only** in a protected operator folder. Use the SHA-256 printed by the baker as `base_sha256` in `vm_config.json`. Any image change requires deliberate repinning.
7. Copy `vm_config.example.json` to `vm_config.json` and update your local paths. The sample permits **only** FSDL and Mini Task at the known commit SHAs. A run requires *both* a matching allowlist entry and `--run-reviewed-fixture`.

**One-time trusted image bake example** (replace paths and SHA with your actual downloaded Ubuntu image and the matching official checksum):

```powershell
python -m projectiq_executioner.phase2.vm_worker.template_bake --ubuntu-image "C:/ProjectIQ/workers/ubuntu-24.04-cloudimg-amd64.img" --ubuntu-sha256 "<OFFICIAL_64_CHARACTER_SHA256>" --out-template "C:/ProjectIQ/workers/ubuntu-24.04-projectiq-v1.qcow2" --allow-trusted-template-network
```

The baker's `--allow-trusted-template-network` flag is deliberately explicit; the image-building phase is the *only* stage that permits outbound package downloads, and there is **no student project code in that VM**. Runtime student-fixture VMs always use `restrict=on`. Stop and investigate if the baker reports a sealing failure or unrestricted sudo on the test boot; do not reuse or manually approve that image.

> The template baker is implemented but has **not** been tested against a real WHPX guest from this environment. First successful Windows boot, Ubuntu package installation, image sealing, and VM execution are the next hardware-specific acceptance tests. The Python tests validate orchestrator logic and guest adapters, not production attestation.

### CLI commands (PowerShell, ProjectIQ repository root)

**Plan FSDL (safe, no QEMU or VM required):**

```powershell
python -m projectiq_executioner.phase2.vm_worker --manifest outputs/executioner/fsdl_phase1/manifest.json --plan outputs/executioner/fsdl_phase2_plan.json --preflight outputs/executioner/fsdl_phase2b.json --out outputs/executioner/fsdl_phase2c_plan.json
```

**Check QEMU tools + approved image hash without launching the VM:**

```powershell
python -m projectiq_executioner.phase2.vm_worker --manifest outputs/executioner/fsdl_phase1/manifest.json --plan outputs/executioner/fsdl_phase2_plan.json --preflight outputs/executioner/fsdl_phase2b.json --vm-config vm_config.json --probe-vm --out outputs/executioner/fsdl_phase2c_probe.json
```

**Execute approved FSDL in a disposable VM (only after a tested golden image exists):**

```powershell
python -m projectiq_executioner.phase2.vm_worker --manifest outputs/executioner/fsdl_phase1/manifest.json --plan outputs/executioner/fsdl_phase2_plan.json --preflight outputs/executioner/fsdl_phase2b.json --vm-config vm_config.json --run-reviewed-fixture --out outputs/executioner/fsdl_phase2c_run.json
```

**Execute approved PHP/MySQL Mini Task:**

```powershell
python -m projectiq_executioner.phase2.vm_worker --manifest outputs/executioner/mini_collab_phase1/manifest.json --plan outputs/executioner/mini_collab_phase2_plan.json --preflight outputs/executioner/mini_collab_phase2b.json --vm-config vm_config.json --run-reviewed-fixture --out outputs/executioner/mini_collab_phase2c_run.json
```

**Expected success** is `STARTUP_VERIFIED`, `HTTP_200_HTML_PROBE_PASSED` and `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`. Note this is **startup/readiness**, not a complete functional or security test.

## Inputs, state transitions and failure policy

* A pinned SHA is copied through Phase 1 -> Phase 2A -> Phase 2B -> Phase 2C; cross-check failures stop preparation.
* GitHub public archive is downloaded by SHA over HTTPS and defensively checked for path traversal, symlinks, bombs, and oversized members. It is rechecked inside the guest. The controller **never imports student modules, installs student dependencies, or runs project scripts on Windows**.
* `plan` -> `CANDIDATE_NEEDS_VM_VERIFICATION` or `NEEDS_ADAPTER`.
* `probe` -> verifies local QEMU tools and the pinned image digest. It is **not attestation**.
* `run` -> requires operator-approved `Owner/Repo@SHA`, isolated VM overlay and restrictions. VM runs trusted guest agent, returns `STARTUP_VERIFIED`, `STARTUP_FAILED`, or `INFRASTRUCTURE_OR_ADAPTER_FAILURE` with deliberately bounded result data.
* A daemon-level **wall timer** kills QEMU if total VM execution exceeds the configured deadline. All success/failure paths terminate QEMU and discard overlay, seed ISO and SSH identity.
* No production database passwords, cloud tokens, real student data, or source-provided shell commands are authorized. No live cloud resources are contacted.

## What must be verified before untrusted production submissions

| Gate | Current pilot | Production requirement |
|---|---|---|
| Native VM boundary | QEMU provider implemented; host boot untested | Hypervisor and golden-image acceptance tests |
| Source provenance | Repository + pinned SHA | Signed/verified snapshots where applicable |
| Guest network | `restrict=on` and loopback SSH forwarding configured | Empirical DNS, TCP, IPv6 and host escape tests |
| Filesystem | No host mounts, QCOW2 overlay | Check guest device access, overlay quota and absence of shared folders |
| Resources | QEMU RAM/CPU and wall timer | Measure disk, process, I/O, network and OOM enforcement |
| Secrets | Ephemeral test DB credentials | Redaction tests and guest sanitization |
| Cleanup | Terminate QEMU and remove temporary directory | Chaos tests for crash, cancel, host restart and orphan VMs |
| Guest integrity | Base SHA pinning | Operator signing, patching and SSH host identity policy |
| Concurrency | One run per invocation | Scheduler, quotas, queues, deny-sprawl |
| Supported apps | Static HTML; one PDO/MySQL pattern | Versioned adapters, metadata-driven configuration, dependencies and test contracts |

**Security caveat:** There is currently a trust-on-first-use SSH host-key exchange on a unique localhost-forwarded port. This is acceptable only for this reviewed-fixture development pilot; production requires stronger worker identity and remote attestation or verifiable per-run host-key provisioning. A local Windows workstation with QEMU is not automatically a trusted multi-tenant grader.

## Practical implementation roadmap

* **2C.1 (implemented):** VM-only candidate compiler, SHA consistency check, operator toolchain and immutable image verification, QEMU per-job overlays, ephemeral SSH seed, bounded controls and cleanup.
* **2C.2 (implemented in code; real-VM smoke pending):** FSDL static site adapter with HTML/HTTP readiness verification.
* **2C.3 (implemented in code; real-VM smoke pending):** PHP/MySQL guest adapter, test-only database and schema import, conservative in-guest PDO credential rewriting.
* **2C.4 (baker implemented; real Windows smoke next):** Bake and validate a golden image using the included operator-controlled workflow, run genuine FSDL and Mini Task smoke, fix observed WHPX/cloud-init incompatibilities, adversarial failure tests.
* **2C.5 (after those):** Python/FastAPI and Next.js adapters, optional local S3 emulator, mock API adapter; parallel-worker scheduling and production sandbox hardening.

## Efficiency decisions

One trusted VM template is reused **without mutation**, and per-job qcow2 overlays are cheap. Native services run together *inside one VM* for simple sites, instead of always requiring a separate Docker container per process. No app dependency download occurs at execution time. VM memory defaults to 1 GiB for static HTML and 3 GiB for PHP/MySQL with 2 vCPUs; tune after measuring resource usage. Close Docker Desktop if it competes for memory on a 16 GiB laptop. For production volume, use a VM worker pool on a dedicated host, not your developer laptop.
