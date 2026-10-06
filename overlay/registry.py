"""The revision-aware live rule set.

Built-in procedures are combined with human-published database procedures and
dictionary entries. Database triggers advance one monotonic registry revision
for every behavior-changing publication/dictionary mutation. Each process
checks that revision and atomically swaps an immutable snapshot when stale.
Drafts remain inert until publication.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from . import _system2
from .domain import DictionaryEntry

_AUGMENT_FIELD = "description"


@dataclass(frozen=True)
class RegistrySnapshot:
    procedures: tuple[Any, ...]
    origin: Mapping[str, str]
    dictionary: tuple[DictionaryEntry, ...]
    load_errors: tuple[str, ...]
    revision: int


class ProcedureRegistry:
    """Assembles one coherent procedure/dictionary snapshot per DB revision."""

    def __init__(self, store, procedures_path: str | Path | None = None) -> None:
        self.store = store
        self.procedures_path = procedures_path
        self._reload_lock = threading.RLock()
        self._snapshot = RegistrySnapshot((), MappingProxyType({}), (), (), -1)
        self.reload()

    def _assemble(self) -> tuple[list[Any], dict[str, str], list[DictionaryEntry], list[str]]:
        errors: list[str] = []
        procedures: list[Any] = []
        origin: dict[str, str] = {}

        try:
            base = _system2.load_procedures(self.procedures_path)
        except Exception as exc:
            errors.append(f"built-in procedures failed to load: {exc}")
            base = []

        for procedure in base:
            procedures.append(procedure)
            origin[procedure.procedure_id] = "built-in"

        for row in self.store.list_published(only_enabled=True):
            payload = row["payload"]
            try:
                procedure = _system2.Procedure.from_dict(payload)
                validation = procedure.validate()
                if validation:
                    errors.append(
                        f"published '{row['procedure_id']}' is invalid: "
                        + "; ".join(validation)
                    )
                    continue
            except Exception as exc:
                errors.append(
                    f"published '{row['procedure_id']}' could not be loaded: {exc}"
                )
                continue

            existing = next(
                (
                    index
                    for index, candidate in enumerate(procedures)
                    if candidate.procedure_id == procedure.procedure_id
                ),
                None,
            )
            if existing is None:
                procedures.append(procedure)
            else:
                procedures[existing] = procedure
            origin[procedure.procedure_id] = f"published by {row['published_by']}"

        dictionary = self.store.list_dictionary(only_enabled=True)
        return procedures, origin, dictionary, errors

    def reload(self) -> int:
        """Rebuild against a stable database revision and atomically publish it."""

        with self._reload_lock:
            final = None
            for _attempt in range(5):
                before = self.store.get_registry_revision()
                assembled = self._assemble()
                after = self.store.get_registry_revision()
                final = (*assembled, after)
                if before == after:
                    break
            assert final is not None
            procedures, origin, dictionary, errors, revision = final
            if before != after:
                errors.append(
                    "procedure registry changed repeatedly during reload; "
                    "the next request will recheck it"
                )
            self._snapshot = RegistrySnapshot(
                procedures=tuple(procedures),
                origin=MappingProxyType(dict(origin)),
                dictionary=tuple(dictionary),
                load_errors=tuple(errors),
                revision=revision,
            )
            return len(procedures)

    def snapshot(self) -> RegistrySnapshot:
        current = self._snapshot
        revision = self.store.get_registry_revision()
        if revision != current.revision:
            self.reload()
        return self._snapshot

    @property
    def procedures(self) -> list[Any]:
        return list(self.snapshot().procedures)

    @property
    def load_errors(self) -> list[str]:
        return list(self.snapshot().load_errors)

    def origin(self, procedure_id: str) -> str:
        return self.snapshot().origin.get(procedure_id, "unknown")

    def get(self, procedure_id: str):
        return next(
            (
                procedure
                for procedure in self.snapshot().procedures
                if procedure.procedure_id == procedure_id
            ),
            None,
        )

    @property
    def dictionary(self) -> list[DictionaryEntry]:
        return list(self.snapshot().dictionary)

    def augment_ticket(
        self,
        ticket: dict[str, Any],
        snapshot: RegistrySnapshot | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Append canonical terms for aliases using one coherent snapshot."""

        current = snapshot or self.snapshot()
        if not current.dictionary:
            return ticket, []

        haystack = " ".join(
            str(ticket.get(field, "") or "")
            for field in ("short_description", "description", "category")
        ).lower()

        applied: list[str] = []
        for entry in current.dictionary:
            if entry.canonical.lower() in haystack:
                continue
            for alias in entry.aliases:
                alias = alias.strip().lower()
                if alias and re.search(
                    rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", haystack
                ):
                    applied.append(entry.canonical)
                    break

        if not applied:
            return ticket, []

        augmented = dict(ticket)
        terms = sorted(set(applied))
        augmented[_AUGMENT_FIELD] = (
            f"{augmented.get(_AUGMENT_FIELD, '') or ''} {' '.join(terms)}".strip()
        )
        return augmented, terms

    def describe(self) -> list[dict[str, Any]]:
        current = self.snapshot()
        return [
            {
                "procedure_id": procedure.procedure_id,
                "name": procedure.name,
                "origin": current.origin.get(procedure.procedure_id, "unknown"),
                "version": _system2.procedure_version(procedure),
            }
            for procedure in current.procedures
        ]
