"""Frozen-corpus guard: the live article set must equal the manifest before anything is evaluated."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

EXPECTED_DIGEST = "54168ae614825f07ccd41598786e3623"
DEFAULT_MANIFEST = Path(r"C:\Users\anjan\xenom_backups\phase0_1_20260920_224953\export\PROTECTED_CORPUS_MANIFEST.txt")


class CorpusDriftError(RuntimeError):
    pass


def digest(ids) -> str:
    """md5 of sorted IDs joined by newline (the form recorded in the manifest)."""
    return hashlib.md5("\n".join(sorted(map(str, ids))).encode()).hexdigest()


@dataclass(frozen=True)
class Manifest:
    stored_digest: str
    frozen_at: str
    statuses: dict  # article_id -> processing_status at freeze time

    @property
    def ids(self) -> set:
        return set(self.statuses)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Manifest:
    stored = frozen = ""
    statuses: dict = {}
    body = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if body:
            if line.strip():
                aid, _, status = line.strip().partition(",")
                statuses[aid] = status
        elif line.startswith("---"):
            body = True
        elif line.startswith("corpus_digest_md5"):
            stored = line.rsplit(":", 1)[1].strip()
        elif line.startswith("frozen_at_utc"):
            frozen = line.split(":", 1)[1].strip()
    return Manifest(stored, frozen, statuses)


def verify_corpus(manifest: Manifest, live_statuses: dict, expected_digest: str = EXPECTED_DIGEST) -> str:
    """Raise CorpusDriftError unless manifest and live DB agree. Returns the verified digest."""
    if manifest.stored_digest != expected_digest:
        raise CorpusDriftError(f"manifest digest {manifest.stored_digest} != expected {expected_digest}")
    if digest(manifest.ids) != expected_digest:
        raise CorpusDriftError("manifest ID list does not hash to the expected digest")
    live_ids = set(live_statuses)
    if live_ids != manifest.ids:
        raise CorpusDriftError(
            f"live corpus drifted: {len(live_ids - manifest.ids)} new, {len(manifest.ids - live_ids)} missing"
        )
    changed = [i for i, s in live_statuses.items() if manifest.statuses[i] != s]
    if changed:
        raise CorpusDriftError(f"{len(changed)} articles changed decision since freeze")
    return expected_digest
