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

The runtime loads local GGUF models through `llama-cpp-python` (`import
llama_cpp`) when server mode starts. The model file must exist, but file
existence alone is not readiness: `model_loaded` becomes true only after the
backend constructor returns a live model object that is kept on runtime state.
If `llama_cpp` is not installed, or if backend initialization fails, the process
continues serving with `status: not_ready` and `model_loaded: false`.

Install the optional GGUF backend only on hosts that should load local models:

```bash
pip install -r requirements-optional.txt
```

MyDL can launch the fork with the stable wrapper:

```bash
MYDL_ODYSSEUS_BINARY=/path/to/mydl-odysseus/scripts/mydl-odysseus-runtime
```

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
