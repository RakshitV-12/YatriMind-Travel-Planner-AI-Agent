"""
Lightweight JSON-file checkpoint saver for LangGraph.

Stores every checkpoint in a single JSON file on disk (default:
checkpoints.json next to this module) so no database server is required.
Conversation state per thread_id survives restarts as long as this file
isn't deleted.

Checkpoint/metadata objects contain LangChain message objects and other
non-trivially-JSON-serializable structures, not just plain dicts, so this
uses LangGraph's own JsonPlusSerializer (the same one its built-in savers
use) to turn them into bytes, then base64-encodes those bytes so they fit
inside a JSON text file.

NOTE ON VERSION COMPATIBILITY: this implements the BaseCheckpointSaver
interface as documented for the LangGraph checkpoint API (get_tuple, list,
put, put_writes, plus async aliases). This was written without access to
your exact installed langgraph==1.2.2 package to test against, so please
run the quick check at the bottom of this file's usage notes before relying
on it -- if BaseCheckpointSaver's method signatures changed in your version,
you'll see a clear TypeError/AttributeError at startup or on first request,
not silent data loss. The checkpointer in backend.py falls back to
MemorySaver automatically if constructing this fails.
"""

import base64
import json
import os
import threading
from typing import Any, Iterator, Optional, Sequence, Tuple

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class JSONFileSaver(BaseCheckpointSaver):
    def __init__(self, path: str = "checkpoints.json"):
        super().__init__()
        self.path = path
        self._serde = JsonPlusSerializer()
        self._lock = threading.Lock()
        self._data = self._load()

    # ---------- file I/O ----------

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable file -- start fresh rather than crash.
                return {}
        return {}

    def _save(self) -> None:
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f)
        os.replace(tmp_path, self.path)  # atomic on POSIX and Windows

    # ---------- serialization helpers ----------

    def _dumps(self, obj: Any) -> dict:
        type_, data = self._serde.dumps_typed(obj)
        return {"type": type_, "data": base64.b64encode(data).decode("ascii")}

    def _loads(self, encoded: Any) -> Any:
        if isinstance(encoded, dict) and "data" in encoded:
            type_ = encoded.get("type", "msgpack")
            data = base64.b64decode(encoded["data"].encode("ascii"))
            return self._serde.loads_typed((type_, data))
        elif isinstance(encoded, str):
            data = base64.b64decode(encoded.encode("ascii"))
            try:
                return self._serde.loads_typed(("msgpack", data))
            except Exception:
                return self._serde.loads_typed(("json", data))
        return encoded

    # ---------- BaseCheckpointSaver interface ----------

    def get_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id")

        thread_data = self._data.get(thread_id, {}).get(checkpoint_ns, {})
        if not thread_data:
            return None

        if checkpoint_id is None:
            # LangGraph checkpoint ids are monotonically sortable, so the
            # lexicographically-highest id is the most recent checkpoint.
            checkpoint_id = max(thread_data.keys())

        record = thread_data.get(checkpoint_id)
        if record is None:
            return None

        checkpoint = self._loads(record["checkpoint"])
        metadata = self._loads(record["metadata"])
        parent_config = record.get("parent_config")
        pending_writes = [
            (w["task_id"], w["channel"], self._loads(w["value"]))
            for w in record.get("pending_writes", [])
        ]

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def list(
        self,
        config: Optional[dict],
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        thread_data = self._data.get(thread_id, {}).get(checkpoint_ns, {})

        ids = sorted(thread_data.keys(), reverse=True)
        if before is not None:
            before_id = before.get("configurable", {}).get("checkpoint_id")
            if before_id is not None:
                ids = [i for i in ids if i < before_id]
        if limit is not None:
            ids = ids[:limit]

        for checkpoint_id in ids:
            tup = self.get_tuple(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_id,
                    }
                }
            )
            if tup is not None:
                yield tup

    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> dict:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_id = config.get("configurable", {}).get("checkpoint_id")

        with self._lock:
            self._data.setdefault(thread_id, {}).setdefault(checkpoint_ns, {})
            self._data[thread_id][checkpoint_ns][checkpoint_id] = {
                "checkpoint": self._dumps(checkpoint),
                "metadata": self._dumps(metadata),
                "parent_config": (
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": parent_id,
                        }
                    }
                    if parent_id
                    else None
                ),
                "pending_writes": [],
            }
            self._save()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: dict,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        with self._lock:
            record = self._data.get(thread_id, {}).get(checkpoint_ns, {}).get(checkpoint_id)
            if record is None:
                return
            for channel, value in writes:
                record["pending_writes"].append(
                    {"task_id": task_id, "channel": channel, "value": self._dumps(value)}
                )
            self._save()

    # ---------- async aliases (sync-backed) ----------
    # backend.py calls travel_graph.invoke(), not .ainvoke(), so async is
    # never actually exercised. These exist only so the class satisfies
    # BaseCheckpointSaver's full interface if your installed LangGraph
    # version requires the async methods to be present/overridden.

    async def aget_tuple(self, config):
        return self.get_tuple(config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        for tup in self.list(config, filter=filter, before=before, limit=limit):
            yield tup

    async def aput(self, config, checkpoint, metadata, new_versions):
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id):
        return self.put_writes(config, writes, task_id)
