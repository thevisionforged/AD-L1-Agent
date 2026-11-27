"""Knowledge base loader and hybrid retrieval: BM25 keyword + OpenAI embeddings, fused with RRF.

Each KB article is a Markdown file with sections marked `## [sN] Heading`.
Every section becomes one retrievable chunk with a stable citation ID such as
`KB-AD-001#s3`. Retrieval is BM25 (Okapi) with a light title/category boost, which
reliably finds exact IT terms (lockout, MFA, VPN, 4740). When an embedder is configured
(online mode), dense vectors (text-embedding-3-small, cached on disk) catch paraphrases
("my phone keeps kicking me out"), and the two rankings are fused with Reciprocal Rank
Fusion. Without an embedder it is BM25 only, so offline mode is deterministic.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

Embedder = Callable[[list[str]], list[list[float]]]

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "it", "my", "i", "me",
    "you", "your", "with", "be", "can", "cant", "can't", "not", "this", "that", "at", "as", "are",
    "do", "does", "have", "has", "was", "from", "by", "if", "so", "please", "help", "get", "how",
}
# Small synonym map so user wording matches KB wording.
_SYNONYMS = {
    "locked": ["lockout"], "lock": ["lockout"], "lockout": ["locked"],
    "2fa": ["mfa"], "authenticator": ["mfa"], "otp": ["mfa"], "phone": ["mfa"],
    "pwd": ["password"], "passcode": ["password"], "forgot": ["reset"], "forgotten": ["reset"],
    "share": ["shared", "folder"], "drive": ["folder", "share"], "permission": ["access"], "denied": ["access"],
    "contractor": ["expired"], "expire": ["expired"], "expiring": ["expired"],
    "printer": ["printer"], "mail": ["outlook"], "email": ["outlook"],
}


def tokenize(text: str) -> list[str]:
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    out: list[str] = []
    for t in toks:
        out.append(t)
        out.extend(_SYNONYMS.get(t, []))
    return out


@dataclass(frozen=True)
class Chunk:
    source_id: str      # e.g. KB-AD-001#s3
    article_id: str     # e.g. KB-AD-001
    title: str          # article title
    section: str        # section heading
    category: str
    subcategory: str
    text: str

    def as_dict(self, max_chars: int = 900) -> dict:
        return {
            "source_id": self.source_id,
            "article": f"{self.article_id}: {self.title}",
            "section": self.section,
            "text": self.text[:max_chars],
        }


_HEAD = re.compile(r"^# (KB-[A-Z]+-\d+): (.+)$", re.M)
_META = re.compile(r"^Category: (.+?) \| Subcategory: (.+)$", re.M)
_SECTION = re.compile(r"^## \[(s\d+)\] (.+)$", re.M)


def load_chunks(kb_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(kb_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        head = _HEAD.search(raw)
        if not head:
            continue
        article_id, title = head.group(1), head.group(2).strip()
        meta = _META.search(raw)
        category, subcategory = (meta.group(1).strip(), meta.group(2).strip()) if meta else ("", "")
        sections = list(_SECTION.finditer(raw))
        for i, m in enumerate(sections):
            end = sections[i + 1].start() if i + 1 < len(sections) else len(raw)
            body = raw[m.end():end].strip()
            chunks.append(Chunk(f"{article_id}#{m.group(1)}", article_id, title, m.group(2).strip(),
                                category, subcategory, body))
    return chunks


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class KnowledgeBase:
    def __init__(self, kb_dir: Path, k1: float = 1.4, b: float = 0.75, extra_dirs: tuple[Path, ...] = ()):
        self.embedder: Embedder | None = None
        self._vectors: list[list[float]] | None = None
        self._embed_args: tuple | None = None
        self.retrieval_mode = "bm25"
        self.kb_dir, self.extra_dirs = kb_dir, extra_dirs
        self.k1, self.b = k1, b
        self._build()

    def reload(self) -> None:
        """Re-index after an approved KB publish (overlay articles override base ones with the same ID)."""
        self._build()
        if self._embed_args:
            self.enable_embeddings(*self._embed_args)

    def _build(self) -> None:
        chunks = {c.source_id: c for c in load_chunks(self.kb_dir)}
        for d in self.extra_dirs:
            if d.exists():
                overlay = load_chunks(d)
                for art in {c.article_id for c in overlay}:
                    chunks = {k: v for k, v in chunks.items() if v.article_id != art}
                chunks.update({c.source_id: c for c in overlay})
        self.chunks = list(chunks.values())
        if not self.chunks:
            raise RuntimeError(f"No KB articles found in {self.kb_dir}")
        self.by_id = {c.source_id: c for c in self.chunks}
        self._docs = [tokenize(f"{c.title} {c.section} {c.subcategory} {c.text}") for c in self.chunks]
        self._title_toks = [set(tokenize(f"{c.title} {c.subcategory}")) for c in self.chunks]
        self._tf = [Counter(d) for d in self._docs]
        self._avgdl = sum(len(d) for d in self._docs) / len(self._docs)
        df: Counter = Counter()
        for d in self._docs:
            df.update(set(d))
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    # ---------------------------------------------------------------- dense vectors
    def enable_embeddings(self, embedder: Embedder, cache_path: Path, model: str) -> bool:
        """Embed every chunk once (disk-cached by content hash). Returns False and stays BM25-only on any error."""
        texts = [f"{c.title} - {c.section}\n{c.text}" for c in self.chunks]
        keys = [hashlib.sha256(f"{model}|{t}".encode()).hexdigest() for t in texts]
        cache: dict[str, list[float]] = {}
        try:
            if cache_path.exists():
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            missing = [i for i, k in enumerate(keys) if k not in cache]
            if missing:
                vecs = embedder([texts[i] for i in missing])
                for i, v in zip(missing, vecs):
                    cache[keys[i]] = v
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
            self._vectors = [cache[k] for k in keys]
            self.embedder = embedder
            self._embed_args = (embedder, cache_path, model)
            self.retrieval_mode = "hybrid"
            return True
        except Exception:  # noqa: BLE001 - any failure leaves deterministic BM25 in place
            self._vectors, self.embedder, self.retrieval_mode = None, None, "bm25"
            return False

    def search(self, query: str, top_k: int = 4, min_score: float = 1.0) -> list[tuple[Chunk, float]]:
        bm25 = self.search_bm25(query, top_k=max(top_k, 8), min_score=min_score)
        if not self.embedder or not self._vectors:
            return bm25[:top_k]
        try:
            qv = self.embedder([query])[0]
        except Exception:  # noqa: BLE001 - embedding outage: degrade to BM25
            return bm25[:top_k]
        dense = sorted(((i, _cos(qv, v)) for i, v in enumerate(self._vectors)), key=lambda x: x[1], reverse=True)
        dense = [(i, s) for i, s in dense[:8] if s >= 0.30]
        # Reciprocal Rank Fusion (k=60)
        fused: dict[str, float] = {}
        for rank, (c, _) in enumerate(bm25):
            fused[c.source_id] = fused.get(c.source_id, 0) + 1 / (60 + rank + 1)
        for rank, (i, _) in enumerate(dense):
            sid = self.chunks[i].source_id
            fused[sid] = fused.get(sid, 0) + 1 / (60 + rank + 1)
        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [(self.by_id[sid], round(score * 1000, 3)) for sid, score in ranked]

    def search_bm25(self, query: str, top_k: int = 4, min_score: float = 1.0) -> list[tuple[Chunk, float]]:
        q = tokenize(query)
        if not q:
            return []
        scores: list[tuple[int, float]] = []
        for i, tf in enumerate(self._tf):
            dl = len(self._docs[i])
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self._idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
            # Boost chunks whose article title matches query terms (helps pick the right article).
            s += 0.6 * len(self._title_toks[i] & set(q))
            if s > 0:
                scores.append((i, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [(self.chunks[i], round(s, 3)) for i, s in scores[:top_k] if s >= min_score]

    def article_sections(self, article_id: str) -> list[Chunk]:
        return [c for c in self.chunks if c.article_id == article_id]
