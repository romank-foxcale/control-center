**Verdict: GO** - all three stacks lock on win-64, linux-64 and osx-arm64 and start on win-64 and linux-64, with one Windows-only .NET 8 defect that has a one-variable workaround (see Problems, P1).

# Spike S3: pixi as the native toolchain provider

Date: 2026-11-07. Question: can pixi (conda-forge) provide the Java + PostgreSQL, .NET and Python toolchains natively on three OSes, without Docker or WSL?

## Setup

- pixi **0.84.1** (latest release), downloaded from `github.com/prefix-dev/pixi/releases/download/v0.84.1/`, run by full path. SHA-256 checked against the digest published with the release:
  - `pixi-x86_64-pc-windows-msvc.exe` `c70256c52b93f8012282bac0986c35f31731ff3a74298c8ed047d11a7b997199` (match)
  - `pixi-x86_64-unknown-linux-musl` `dea64d690020095e70a7194f48a520fb31cf70daef76d8ba38bbcf874b8c84a2` (match)
- `PIXI_HOME` and `PIXI_CACHE_DIR` set inside the scratch folder. Nothing installed globally; no admin, PATH or registry change.
- Hosts: Windows 11 Pro 10.0.26200 (win-64, Git Bash); WSL Ubuntu on the same machine (linux-64, ext4 home, 14 cores). No macOS host: osx-arm64 is lock-only.
- Linux installs used `pixi install --locked` with the lock file made on Windows: one lock file serves all platforms.
- One workspace per stack, channel `conda-forge` only.

## Result table

| Stack | Platform | Solves | Installs | Starts | Versions |
|---|---|---|---|---|---|
| Java + PostgreSQL | win-64 | yes | yes | yes: `java -version`, `mvn -v`, `gradle --version`, initdb / pg_ctl start (port 55432) / psql / pg_ctl stop | openjdk 21.0.10 (Azul Zulu build), postgresql 18.6, maven 3.10.0, gradle 9.3.1 |
| Java + PostgreSQL | linux-64 | yes | yes | yes: same commands | openjdk 21.0.10 (conda-forge source build, `21.0.10-internal`), postgresql 18.6, maven 3.10.0, gradle 9.3.1 |
| Java + PostgreSQL | osx-arm64 | yes | not tested | not tested | openjdk 21.0.10, **postgresql 18.4**, maven 3.10.0, gradle 9.3.1 |
| .NET 8 | win-64 | yes | yes | **with workaround**: `dotnet new console` creates files, restore/build fail with MSB4246 (P1); build and run pass with `MSBuildEnableWorkloadResolver=false` | dotnet-sdk 8.0.425, runtime 8.0.31 |
| .NET 8 | linux-64 | yes | yes | yes: new console, build, run, offline | dotnet-sdk 8.0.425 |
| .NET 8 | osx-arm64 | yes | not tested | not tested | dotnet-sdk 8.0.425 |
| .NET 10 | win-64 | yes | yes | yes: new console, build, run, offline | dotnet-sdk 10.0.401 |
| .NET 10 | linux-64 | yes | yes | yes | dotnet-sdk 10.0.401 |
| .NET 10 | osx-arm64 | yes | not tested | not tested | dotnet-sdk 10.0.401 |
| Python | win-64 | yes | yes | yes: one-line pytest passes | python 3.12.16, pytest 9.2.0 |
| Python | linux-64 | yes | yes | yes | python 3.12.16, pytest 9.2.0 |
| Python | osx-arm64 | yes | not tested | not tested | python 3.12.16, pytest 9.2.0 |

.NET offline: each test app had a `nuget.config` with `<clear />` package sources. `dotnet new console`, restore and build passed with no package source, so the SDK packages carry the templates and targeting packs. A project with NuGet dependencies will still need network access or a feed.

## Timings

Wall clock of `pixi install --all` (all environments of the workspace). "First" = empty cache, downloads included. "Cached" = after `pixi clean`, cache kept.

| Stack | win-64 first | win-64 cached | linux-64 first | linux-64 cached |
|---|---|---|---|---|
| Python | 33.9 s | 6.2 s | 3.3 s | 3.4 s |
| .NET (8 + 10, two envs) | 60.5 s | 17.0 s | 38.4 s | 1.7 s |
| Java + PG + Maven + Gradle | 42.9 s | 8.4 s | 26.8 s | 7.6 s |

`pixi lock` for all three platforms: 0.8 to 2.9 s per workspace.

Windows installs are slower mainly because of file linking on NTFS (Defender likely adds to it; not measured).

## Disk sizes

Envs are hardlinked from the cache on both OSes (`java` binary has 2 links), so an env costs little beyond the cache when both are on the same volume.

| Item | win-64 | linux-64 |
|---|---|---|
| Package cache (all three stacks) | 2.0 GB | 2.1 GB |
| Cache + all envs together (real disk) | 2.1 GB | 2.4 GB |
| Python env (apparent size) | 149 MB | 78 MB not shared with the cache |
| .NET 8 env (apparent) | 611 MB | mostly shared |
| .NET 10 env (apparent) | 661 MB | mostly shared |
| Java env (apparent) | 652 MB | 186 MB not shared with the cache |

Large single packages: openjdk about 175 MB, dotnet-sdk 120 to 137 MB, gradle 128 MB.

## Problems met

**P1. .NET 8 SDK on win-64: restore and build fail.** `dotnet new console` creates the files, then restore fails:

```
...\dotnet\.pixi\envs\default\dotnet\sdk\8.0.425\Sdks\Microsoft.NET.Sdk\targets\Microsoft.NET.Sdk.ImportWorkloads.props(14,3): error MSB4246: SDK Resolver folder exists but without an SDK Resolver DLL or manifest file. This may indicate a corrupt or invalid installation of MSBuild. SDK resolver path: ...\dotnet\.pixi\envs\default\dotnet\sdk\8.0.425\SdkResolvers\Microsoft.NET.Sdk.WorkloadMSBuildSdkResolver
```

and `dotnet run` then reports `'...\hello.csproj' is not a valid project file.`

- The conda-forge package replaces the resolver DLL in that folder with a manifest `Microsoft.NET.Sdk.WorkloadMSBuildSdkResolver.xml` that points to `..\..\Microsoft.NET.Sdk.WorkloadMSBuildSdkResolver.dll`. The DLL is present at that path.
- The same layout works with SDK 10 on Windows (MSBuild 18) and with SDK 8 on Linux. So MSBuild 17.11 on Windows does not accept this manifest. The root cause in MSBuild was not confirmed. The resolved path is 253 characters, under MAX_PATH, so path length is not the obvious cause.
- Workaround, verified: set the environment variable `MSBuildEnableWorkloadResolver=false` (or pass `-p:MSBuildEnableWorkloadResolver=false`). Build and `dotnet run` then pass. A project that needs .NET workloads (MAUI, wasm, Aspire workload) will not work with this workaround.
- Alternative: use SDK 10 to build `net8.0` targets (not tested in this spike).

**P2. Windows: deleting an env with PowerShell 5.1 fails.** `Remove-Item -Recurse -Force` on a .NET env failed with `Could not find a part of the path 'Microsoft.Extensions.Configuration.Binder.SourceGeneration.resources.dll'` and `The directory is not empty.` The host has `LongPathsEnabled = 0`, and the .NET packs have deep paths. `pixi clean` and `pixi clean cache --yes` removed everything without error. Adapters must delete envs with `pixi clean`, not with shell commands.

**P3. Version skew across platforms.** With `postgresql = "*"`, osx-arm64 solved to 18.4 while win-64 and linux-64 got 18.6 (newer builds are not yet published for osx-arm64). Pin at least the minor version per stack, or accept the skew knowingly.

**P4. Different JDK builds per platform.** win-64 gets an Azul Zulu repackage; linux-64 gets a conda-forge source build that reports `21.0.10-internal`. Both pass `java -version`; tools that parse the version string must accept both.

**P5. A system-wide .NET exists in `C:\Program Files\dotnet`.** The pixi env stays isolated: activation sets `DOTNET_ROOT` to the env and `DOTNET_MULTILEVEL_LOOKUP=0`, and `dotnet --info` lists only the env SDK. Running `dotnet` outside `pixi run` gets the system one.

**P6. Background servers.** `dotnet build` leaves an MSBuild server and `VBCSCompiler` running from the env. `dotnet build-server shutdown` stopped them. A gate must run it at the end, or a later `pixi clean` can fail on locked files.

**P7. PostgreSQL on Linux needs a socket directory.** The test passed `-o "-p 55432 -k <dir>"` to keep the Unix socket out of `/var/run/postgresql`, which a normal user may not write to. On Windows only `-p` was needed. initdb used `-A trust` for the test only.

Not a problem but worth noting: pixi also downloads a `uv-cache` folder in the cache dir; it stayed empty here because no PyPI dependencies were used.

## What a later milestone must add

1. A pixi adapter that runs every gate as `pixi run --manifest-path <ws>/pixi.toml -e <env> ...` with `PIXI_HOME` and `PIXI_CACHE_DIR` set, and with `pixi install --locked` so the committed lock file is the contract (one lock file covers all three platforms).
2. For .NET 8 on Windows: set `MSBuildEnableWorkloadResolver=false` in the env (`[activation.env]` in pixi.toml) until conda-forge fixes the package, or build with SDK 10. Report the defect upstream to the conda-forge `dotnet` feedstock.
3. Gate teardown: `dotnet build-server shutdown`; `pg_ctl stop` for every started cluster; free-port selection for PostgreSQL; a socket dir on Unix.
4. Env removal only through `pixi clean`.
5. A real osx-arm64 run (CI runner or a Mac): this spike proves only that it solves.
6. NuGet, Maven and Gradle dependency downloads need network or a mirror; pixi provides only the toolchains. Their caches (`NUGET_PACKAGES`, `~/.m2`, `GRADLE_USER_HOME`) should also be pointed into the CC's local data.
7. No Docker backend is needed for these three stacks.

## pixi.toml files used

Java + PostgreSQL:

```toml
[workspace]
name = "s3-java"
channels = ["conda-forge"]
platforms = ["win-64", "linux-64", "osx-arm64"]

[dependencies]
openjdk = "21.*"
postgresql = "*"
maven = "*"
gradle = "*"
```

.NET:

```toml
[workspace]
name = "s3-dotnet"
channels = ["conda-forge"]
platforms = ["win-64", "linux-64", "osx-arm64"]

[feature.net8.dependencies]
dotnet-sdk = "8.*"

[feature.net10.dependencies]
dotnet-sdk = "10.*"

[environments]
default = ["net8"]
net10 = ["net10"]
```

Python:

```toml
[workspace]
name = "s3-python"
channels = ["conda-forge"]
platforms = ["win-64", "linux-64", "osx-arm64"]

[dependencies]
python = "3.12.*"
pytest = "*"
```

## Commands that ran

- Java/PG (win-64): `pixi run initdb -D <dir> -U postgres -A trust --no-locale -E UTF8`, `pixi run pg_ctl -D <dir> -o "-p 55432" -l <log> -w start`, `pixi run psql -h localhost -p 55432 -U postgres -c "select version()"`, `pixi run pg_ctl -D <dir> -w stop`.
- .NET: `dotnet new console -n hello -o .`, `dotnet build -c Release`, `dotnet run -c Release --no-build`, with `DOTNET_CLI_HOME` and `NUGET_PACKAGES` inside the scratch folder.
- Python: `pixi run pytest -q -p no:cacheprovider <dir>` on `def test_one(): assert 1 + 1 == 2`.

## Not verified

- Any install or run on osx-arm64.
- Gradle and Maven builds of a real project (only `--version` ran).
- Whether SDK 10 builds `net8.0` targets on Windows.
- Behaviour on a Windows host with `LongPathsEnabled = 1`, or behind a proxy or with no network.
- Cleanup state: the WSL folder `~/s3-pixi` is deleted. The Windows scratch folder is left in the session scratchpad. PostgreSQL is stopped on both hosts, and no test process is left running.
