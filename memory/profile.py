"""Firestore-backed profile memory, keyed by user+plan.

Persists plan quirks (e.g. "this payer needs the days_supply field spelled
out on every call", allowed_early_days overrides, prior_attempts notes) so
the second session for the same user+plan is visibly shorter: fields the
agent already learned are pre-filled instead of asked again.

CRITICAL (spec 02): a memory demo without deletion is a governance fail.
`forget_fact` / `correct_fact` are first-class, not an afterthought.

CRITICAL (DESIGN.md ADK limits): `add_session_to_memory` does not await its
extraction LRO. We do NOT use that ADK helper here. We write facts to
Firestore synchronously and read them back synchronously, so there is no
LRO to race in the first place -- the "await before next retrieval" rule
is satisfied by construction, not by a wait loop. We still name this out
loud (see MemoryService docstring below) because it's a known ADK PM pain
point worth calling out on camera.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def profile_key(user_id: str, plan: str) -> str:
    """Deterministic key: same user+plan always resolves to the same profile,
    same reasoning as agentspine's compute_run_id -- no uuid4, no timestamp.
    """
    return f"{user_id.strip().lower()}::{plan.strip().lower()}"


@dataclass
class FactRecord:
    key: str
    value: Any
    source: str  # "user_stated" | "extracted_from_letter" | "corrected"
    updated_at: str
    deleted: bool = False


@dataclass
class ProfileMemory:
    """All remembered facts for one user+plan pair."""

    profile_key: str
    facts: dict[str, FactRecord] = field(default_factory=dict)

    def active_facts(self) -> dict[str, Any]:
        """Facts visible to the agent: excludes anything deleted."""
        return {k: v.value for k, v in self.facts.items() if not v.deleted}


class MemoryService(abc.ABC):
    """Pluggable profile memory store.

    NOTE on the ADK `add_session_to_memory` LRO bug (DESIGN.md): that ADK
    helper kicks off an async memory-extraction long-running operation and
    returns before it completes, so a retrieval immediately after can miss
    facts just written. This class sidesteps the bug entirely by writing
    facts directly and synchronously (no LRO involved) -- `remember_fact`
    returns only after the fact is durably stored, so `active_facts` called
    right after is guaranteed to see it. Where we DO go through an ADK
    memory-extraction step in the future, the rule is: explicitly await the
    LRO handle before the next retrieval, never assume `add_session_to_memory`
    blocked for you.
    """

    @abc.abstractmethod
    def remember_fact(self, user_id: str, plan: str, key: str, value: Any,
                       source: str = "user_stated") -> None:
        ...

    @abc.abstractmethod
    def correct_fact(self, user_id: str, plan: str, key: str, new_value: Any) -> None:
        """Overwrite a previously stored fact. Used when a caregiver says
        "actually my mom's plan changed" or a parsed value was wrong."""
        ...

    @abc.abstractmethod
    def forget_fact(self, user_id: str, plan: str, key: str) -> bool:
        """Explicit deletion path. Returns True if a fact was deleted,
        False if there was nothing to delete. This is the governance
        control the spec calls non-negotiable: a caregiver must be able to
        say "forget that" and have it actually gone from future retrievals.
        """
        ...

    @abc.abstractmethod
    def get_profile(self, user_id: str, plan: str) -> Optional[ProfileMemory]:
        ...


class InMemoryMemoryService(MemoryService):
    """In-process backend for offline tests. Thread-safe.

    Mirrors agentspine.MemoryBackend's shape: same "in-memory for tests,
    Firestore for real" split as the shared spine.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._profiles: dict[str, ProfileMemory] = {}

    def remember_fact(self, user_id: str, plan: str, key: str, value: Any,
                       source: str = "user_stated") -> None:
        pkey = profile_key(user_id, plan)
        with self._lock:
            profile = self._profiles.setdefault(pkey, ProfileMemory(profile_key=pkey))
            profile.facts[key] = FactRecord(
                key=key,
                value=value,
                source=source,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )

    def correct_fact(self, user_id: str, plan: str, key: str, new_value: Any) -> None:
        pkey = profile_key(user_id, plan)
        with self._lock:
            profile = self._profiles.get(pkey)
            if profile is None or key not in profile.facts:
                raise KeyError(f"no fact '{key}' to correct for profile {pkey}")
            profile.facts[key] = FactRecord(
                key=key,
                value=new_value,
                source="corrected",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )

    def forget_fact(self, user_id: str, plan: str, key: str) -> bool:
        pkey = profile_key(user_id, plan)
        with self._lock:
            profile = self._profiles.get(pkey)
            if profile is None or key not in profile.facts:
                return False
            record = profile.facts[key]
            if record.deleted:
                return False
            record.deleted = True
            record.updated_at = datetime.now(timezone.utc).isoformat()
            return True

    def get_profile(self, user_id: str, plan: str) -> Optional[ProfileMemory]:
        pkey = profile_key(user_id, plan)
        with self._lock:
            return self._profiles.get(pkey)


class FirestoreMemoryService(MemoryService):
    """Real Firestore-backed profile memory.

    Collection layout: profiles/{profile_key}/facts/{fact_key}. A deleted
    fact keeps its document (audit trail) but is marked deleted=True and
    filtered out of active_facts -- deletion is a visible state change,
    not a silent no-op, which matters for the "prove deletion happened"
    part of the demo.

    Import of google.cloud.firestore is lazy: offline tests never need
    real credentials.
    """

    def __init__(self, client: Any = None, collection: str = "profiles") -> None:
        if client is None:
            from google.cloud import firestore  # local import, optional dep

            client = firestore.Client()
        self._client = client
        self._collection = collection

    def _facts_ref(self, user_id: str, plan: str):
        pkey = profile_key(user_id, plan)
        return self._client.collection(self._collection).document(pkey).collection("facts")

    def remember_fact(self, user_id: str, plan: str, key: str, value: Any,
                       source: str = "user_stated") -> None:
        doc = self._facts_ref(user_id, plan).document(key)
        doc.set({
            "key": key,
            "value": value,
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "deleted": False,
        })
        # Synchronous set() above is the "await before next retrieval" --
        # there is no LRO here to race, unlike ADK's add_session_to_memory.

    def correct_fact(self, user_id: str, plan: str, key: str, new_value: Any) -> None:
        doc = self._facts_ref(user_id, plan).document(key)
        snapshot = doc.get()
        if not snapshot.exists:
            raise KeyError(f"no fact '{key}' to correct for user={user_id} plan={plan}")
        doc.set({
            "key": key,
            "value": new_value,
            "source": "corrected",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "deleted": False,
        })

    def forget_fact(self, user_id: str, plan: str, key: str) -> bool:
        doc = self._facts_ref(user_id, plan).document(key)
        snapshot = doc.get()
        if not snapshot.exists:
            return False
        data = snapshot.to_dict() or {}
        if data.get("deleted"):
            return False
        doc.update({
            "deleted": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return True

    def get_profile(self, user_id: str, plan: str) -> Optional[ProfileMemory]:
        pkey = profile_key(user_id, plan)
        docs = list(self._facts_ref(user_id, plan).stream())
        if not docs:
            return None
        profile = ProfileMemory(profile_key=pkey)
        for doc in docs:
            data = doc.to_dict() or {}
            profile.facts[data["key"]] = FactRecord(
                key=data["key"],
                value=data.get("value"),
                source=data.get("source", "user_stated"),
                updated_at=data.get("updated_at", ""),
                deleted=data.get("deleted", False),
            )
        return profile
