"""Atomic JSON receipts keep replay events out of every SQLite snapshot."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from sim2clip.agent.state import JSONDict


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError("Invalid artifact run ID")
        return self._contained(self.root / run_id)

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Artifact path is outside the artifact root")
        return resolved

    def write(self, path: Path, data: bytes) -> None:
        path = self._contained(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _receipt_path(self, run_id: str, call: JSONDict) -> Path:
        if not isinstance(call.get("id"), str) or not re.fullmatch(r"r\d+-c\d+", call["id"]):
            raise ValueError("Invalid internal tool call ID")
        digest = hashlib.sha256(json_bytes([call["name"], call["arguments"]])).hexdigest()
        return self._contained(self.run_dir(run_id) / f"{call['id']}-{digest}.receipt.json")

    def save_response(self, run_id: str, call: JSONDict, payload: JSONDict) -> JSONDict:
        receipt = self._receipt_path(run_id, call)
        path = receipt.with_name(receipt.name.replace(".receipt.json", ".json"))
        content = json_bytes(payload)
        self.write(path, content)
        reference = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
        self.write(
            receipt,
            json_bytes(
                {"name": call["name"], "arguments": call["arguments"], "reference": reference}
            ),
        )
        return reference

    def cached(self, run_id: str, call: JSONDict) -> JSONDict | None:
        receipt = self._receipt_path(run_id, call)
        if not receipt.exists():
            return None
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if value["name"] != call["name"] or value["arguments"] != call["arguments"]:
            raise ValueError("Receipt arguments do not match the tool call")
        self.load(value["reference"])
        return dict(value["reference"])

    def load(self, reference: JSONDict) -> JSONDict:
        path = Path(reference["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Artifact reference is outside this run's artifact root")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != reference["sha256"]:
            raise ValueError("Artifact checksum mismatch")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("Artifact must contain a JSON object")
        return value
