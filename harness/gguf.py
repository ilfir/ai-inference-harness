"""Minimal GGUF header reader: metadata keys and the tensor list, without loading weights."""

import re
import struct
from pathlib import Path

_SCALAR = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
_STRING, _ARRAY = 8, 9


class GGUFError(Exception):
    pass


class _Reader:
    def __init__(self, f):
        self.f = f

    def unpack(self, fmt):
        size = struct.calcsize("<" + fmt)
        data = self.f.read(size)
        if len(data) != size:
            raise GGUFError("truncated header")
        return struct.unpack("<" + fmt, data)[0]

    def string(self):
        return self.f.read(self.unpack("Q")).decode("utf-8", "replace")

    def value(self, vtype, keep=True):
        if vtype in _SCALAR:
            return self.unpack(_SCALAR[vtype])
        if vtype == _STRING:
            return self.string()
        if vtype == _ARRAY:
            etype, count = self.unpack("I"), self.unpack("Q")
            if etype in _SCALAR and not keep:
                self.f.seek(struct.calcsize("<" + _SCALAR[etype]) * count, 1)
                return None
            items = [self.value(etype, keep) for _ in range(count)]
            return items if keep else None
        raise GGUFError("unknown value type %d" % vtype)


def read_header(path):
    """Return (metadata dict, [(tensor name, dims, ggml type)]). Large arrays are skipped."""
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise GGUFError("%s: not a GGUF file" % path)
        r = _Reader(f)
        r.unpack("I")  # version
        n_tensors, n_kv = r.unpack("Q"), r.unpack("Q")
        meta = {}
        for _ in range(n_kv):
            key = r.string()
            vtype = r.unpack("I")
            keep = not key.startswith("tokenizer.ggml.")
            meta[key] = r.value(vtype, keep)
        tensors = []
        for _ in range(n_tensors):
            name = r.string()
            dims = [r.unpack("Q") for _ in range(r.unpack("I"))]
            ttype = r.unpack("I")
            r.unpack("Q")  # offset
            tensors.append((name, dims, ttype))
    return meta, tensors


_SPLIT = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$")


def shard_paths(first):
    """All shard paths for a (possibly split) model, given the first shard."""
    first = Path(first)
    m = _SPLIT.match(first.name)
    if not m:
        return [first]
    stem, _, total = m.groups()
    return [first.with_name("%s-%05d-of-%s.gguf" % (stem, i, total)) for i in range(1, int(total) + 1)]
