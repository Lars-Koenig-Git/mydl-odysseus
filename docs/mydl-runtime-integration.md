# MyDL Runtime Integration

The `mydl_odysseus` package contains the external-fork runtime scaffold used by
MyDL's M4 smoke contracts. It reads the generated `odysseus.toml`, validates the
loopback process boundary, and exposes a small embeddable FastAPI surface.

Use validation-only mode while wiring the fork:

```bash
python -m mydl_odysseus --config /path/to/odysseus.toml --check-config
```

Server mode binds only to the configured loopback address and prints
`ODYSSEUS_READY` with the selected bind port only when `/health` would return
`status: ok`. Otherwise it prints `ODYSSEUS_NOT_READY` with a short secret-free
reason.

The runtime loads only Odysseus-managed GGUF models through `llama-cpp-python`
(`import llama_cpp`) when server mode starts. `odysseus.toml` must include this
model provenance contract:

```toml
model_source = "odysseus-cookbook"
model_repo_id = "Qwen/Qwen3-4B-GGUF"
model_file = "Qwen3-4B-Q4_K_M.gguf"
model_path = "/home/user/.cache/huggingface/hub/models--Qwen--Qwen3-4B-GGUF/snapshots/<rev>/Qwen3-4B-Q4_K_M.gguf"
# Optional proof constraints:
# model_cache_root = "/home/user/.cache/huggingface/hub"
# model_snapshot_path = "/home/user/.cache/huggingface/hub/models--Qwen--Qwen3-4B-GGUF/snapshots/<rev>"
```

The path must match the repo/file fields and the Hugging Face cache snapshot
layout produced by Odysseus Cookbook behavior. Arbitrary GGUF paths are rejected,
and projector files such as `mmproj-*.gguf` never count as the main model. File
existence alone is not readiness: `model_loaded` becomes true only after managed
model validation passes and the backend constructor returns a live model object
that is kept on runtime state. Missing managed model files report
`managed_model_missing`; missing `llama_cpp` reports `backend_unavailable`.

Install the optional GGUF backend only on hosts that should load local models:

```bash
pip install -r requirements-optional.txt
```

MyDL can launch the fork with the stable wrapper:

```bash
MYDL_ODYSSEUS_BINARY=/path/to/mydl-odysseus/scripts/mydl-odysseus-runtime
```

Full fork readiness requires a real external fork binary, a real
Odysseus-managed GGUF model, the real llama backend installed, MCP probes ready,
and the brain-store boundary ready.

On startup, server mode probes the configured Hub and Social MCP URLs with the
configured MCP bearer. The probes call only `initialize` and `tools/list`, require
HTTP 200 JSON-RPC object responses, and mark each MCP readiness flag true only
when the expected Odysseus tool names are present. Probe failures are reported
only through the boolean `/health` flags; bearer values and config secret labels
must not appear in stdout, stderr, `/health`, or iframe HTML.

The runtime accepts only the MyDL brain-store key id
`mydl.odysseus.brain-store.v1`. It rejects raw brain-key style fields such as
`brain_key`, `brainKey`, `brainStoreKey`, `brain_store_key`,
`brain_store_key_hex`, `mnemonic`, and `seed`; those values must never be passed
through this process boundary.
