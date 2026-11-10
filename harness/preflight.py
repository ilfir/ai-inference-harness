"""Checks run before starting the backend. Each check yields (status, name, detail)."""

import subprocess

from . import config, gguf, system

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
TABLE_TENSOR = "per_layer_token_embd.weight"
KNOWN_SERVERS = ("ollama", "llama-server", "lms", "LM Studio", "unsloth", "omlx", "mlx")
OS_RESERVE_GB = 2.0  # measured: ~6 GB stayed available at 128k with no swap growth


def _binary(profile):
    if not config.LLAMA_SERVER.exists():
        return FAIL, "llama-server", "%s missing; run: harness build" % config.LLAMA_SERVER
    if profile.get("MTP_FILE"):
        help_text = subprocess.run([str(config.LLAMA_SERVER), "--help"], capture_output=True, text=True).stdout
        if "draft-mtp" not in help_text:
            return FAIL, "llama-server", "this build has no --spec-type draft-mtp"
    return PASS, "llama-server", str(config.LLAMA_SERVER)


def _model(profile):
    first = profile.model_path
    if not first.exists():
        return [(FAIL, "model files", "%s missing; run: harness download %s" % (first, profile.name))], None
    shards = gguf.shard_paths(first)
    missing = [p.name for p in shards if not p.exists()]
    if missing:
        return [(FAIL, "model files", "missing shards: " + ", ".join(missing))], None
    results, headers = [], []
    for p in shards:
        try:
            headers.append(gguf.read_header(p))
        except (gguf.GGUFError, OSError) as e:
            return [(FAIL, "model files", "%s: %s" % (p.name, e))], None
    size_gb = sum(p.stat().st_size for p in shards) / 1e9
    meta = headers[0][0]
    arch = meta.get("general.architecture", "?")
    results.append((PASS, "model files", "%d shard(s), %.1f GB, arch %s" % (len(shards), size_gb, arch)))

    # The n-gram table must sit alone in its shard, or Metal wires it together with the weights.
    for i, (_, tensors) in enumerate(headers):
        names = [t[0] for t in tensors]
        if TABLE_TENSOR in names and len(names) > 1:
            results.append((FAIL, "n-gram table shard",
                            "shard %d mixes the table with %d other tensors; it would be wired and OOM"
                            % (i + 1, len(names) - 1)))
            break
        if TABLE_TENSOR in names:
            table_gb = shards[i].stat().st_size / 1e9
            results.append((PASS, "n-gram table shard", "shard %d holds only the table (%.1f GB, stays on SSD)"
                            % (i + 1, table_gb)))
            break
    else:
        if arch == "qwen4exp":
            results.append((WARN, "n-gram table shard", "table tensor not found"))
    return results, arch


def _mtp(profile, arch):
    path = profile.mtp_path
    if path is None:
        return [(PASS, "MTP head", "not used by this profile")]
    if not path.exists():
        return [(FAIL, "MTP head", "%s missing; run: harness download %s" % (path, profile.name))]
    try:
        meta, tensors = gguf.read_header(path)
    except (gguf.GGUFError, OSError) as e:
        return [(FAIL, "MTP head", str(e))]
    head_arch = meta.get("general.architecture", "?")
    if arch and head_arch != arch:
        return [(FAIL, "MTP head", "arch %s does not match model arch %s" % (head_arch, arch))]
    if not any("nextn" in t[0] for t in tensors):
        return [(WARN, "MTP head", "no nextn tensors found; drafting may be inactive")]
    return [(PASS, "MTP head", "%s (%.2f GB)" % (path.name, path.stat().st_size / 1e9))]


def _mmproj(profile):
    path = profile.mmproj_path
    if path is None:
        return [(PASS, "vision projector", "not used by this profile (text only)")]
    if not path.exists():
        return [(FAIL, "vision projector", "%s missing; run: harness download %s" % (path, profile.name))]
    try:
        meta, _ = gguf.read_header(path)
    except (gguf.GGUFError, OSError) as e:
        return [(FAIL, "vision projector", str(e))]
    arch = meta.get("general.architecture", "?")
    if arch != "clip" or not meta.get("clip.has_vision_encoder"):
        return [(FAIL, "vision projector", "%s is not a vision mmproj (arch %s)" % (path.name, arch))]
    return [(PASS, "vision projector", "%s (%.2f GB)" % (path.name, path.stat().st_size / 1e9))]


def _memory(profile, backend_running):
    results = []
    need_mb = int(profile.gpu_budget_gb * 1024)
    limit = system.wired_limit_mb()
    if limit and limit < need_mb:
        results.append((FAIL, "GPU wired limit",
                        "iogpu.wired_limit_mb=%d < %d needed; run: sudo sysctl iogpu.wired_limit_mb=%d"
                        % (limit, need_mb, need_mb + 2048)))
    elif not limit:
        results.append((WARN, "GPU wired limit",
                        "macOS default; for this profile run: sudo sysctl iogpu.wired_limit_mb=%d" % (need_mb + 2048)))
    else:
        results.append((PASS, "GPU wired limit", "%d MB (profile needs ~%d MB)" % (limit, need_mb)))

    if not backend_running:
        avail = system.available_gb()
        need = profile.gpu_budget_gb + OS_RESERVE_GB
        status = PASS if avail >= need else FAIL
        results.append((status, "free memory", "%.1f GB available, %.1f GB needed" % (avail, need)))

    others = [(pid, gb, cmd) for pid, gb, cmd in system.big_processes()
              if not cmd.endswith("llama-server") or not backend_running]
    if others:
        listed = ", ".join("%s (pid %d, %.1f GB)" % (cmd.rsplit("/", 1)[-1], pid, gb) for pid, gb, cmd in others[:5])
        servers = [c for _, _, c in others if any(k.lower() in c.lower() for k in KNOWN_SERVERS)]
        results.append((WARN, "large processes",
                        listed + ("; stop other model servers first" if servers else "")))
    return results


def run(profile, backend_running=False):
    results = [_binary(profile)]
    model_results, arch = _model(profile)
    results += model_results
    results += _mtp(profile, arch)
    results += _mmproj(profile)
    results += _memory(profile, backend_running)

    if system.port_in_use(profile.port) and not backend_running:
        results.append((FAIL, "port", "port %d is in use" % profile.port))
    else:
        results.append((PASS, "port", str(profile.port)))

    free = system.disk_free_gb(config.HOME)
    results.append((PASS if free >= 10 else WARN, "disk", "%.0f GB free" % free))
    return results


def ok(results):
    return all(r[0] != FAIL for r in results)


def render(results):
    marks = {PASS: "✅", WARN: "⚠️ ", FAIL: "❌"}
    return "\n".join("%s %-20s %s" % (marks[s], name, detail) for s, name, detail in results)
