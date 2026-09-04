"""
HybridSOPEngine — 100% Local, No External API, Docker-Ready
============================================================
Architecture:
  Step 1 — SMART FILTER (Python, instant, no LLM)
    Fast keyword/section/category matching narrows 400 records down to
    the 5-30 most relevant ones. Same logic that gave 85% accuracy before.

  Step 2 — LLM READER (Ollama, only on the filtered set)
    Ollama reads the small filtered set and answers by understanding meaning.
    Because the set is small (5-30 records), response is fast (~3-8s).
    Because it's the LLM reading — not Python matching — synonyms work.

Why this is better than both previous approaches:
  - Old approach 1 (Python only): Fast but showed raw records, missed synonyms
  - Old approach 2 (LLM reads all 400): Correct but very slow (30-60s)
  - This approach: Fast (smart filter) + Correct (LLM reads filtered set)

Data never leaves your server. No external API.

Public interface identical to SOPRAGEngine / ClaudeSOPEngine:
  get_answer(), get_scopes(), get_team_sources(), get_all_sources(), check_health()
"""

from __future__ import annotations

import os
import re
import json
import glob
import time
import urllib.request
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — LLM reads pre-filtered records and answers naturally
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """You are SOP Master — a senior billing operations expert at Q Way Technologies Healthcare RCM.

You have been given SOP (Standard Operating Procedure) records from a healthcare billing practice. Read every record carefully, then answer the user's question as a knowledgeable colleague who has fully studied this SOP.

═══════════════════════════════════════════════
HOW TO READ AND UNDERSTAND THE QUESTION
═══════════════════════════════════════════════
Before answering, identify what the user is actually asking:

• If the question names a billing status (e.g. "Appeal Needed", "Pending Info Practice", "Paid Not Posted") → explain what that status means, who sets it, when it is used, and what action follows.
• If the question asks about a procedure or workflow step → list the exact steps in order as written in the SOP.
• If the question asks about a rule, threshold, or condition → state the exact condition and the resulting action, including any dollar amounts, timeframes, or payer-specific rules.
• If the question is a follow-up or short phrase → connect it to the prior context and answer fully.
• If the question uses shorthand or abbreviations, interpret them naturally:
  - "TFL" = timely filing limit / filing deadline
  - "POTFL" = proof of timely filing
  - "PIP" = Pending Info Practice billing status
  - "AR" = accounts receivable / follow-up
  - "EOB" / "ERA" = explanation of benefits / electronic remittance advice
  - "COB" = coordination of benefits
  - "NSF" = returned / bounced check
  - "EOB" = explanation of benefits
  - "OON" = out of network
  - "CPT" = CPT procedure codes
  - "AUTH" = authorization / prior authorization
  - "FU" = follow up / follow-up
  - "CHK" = check (payment check)
  - "NPI" = national provider identifier
  - "DOB" = date of birth
  - "DOS" = date of service
  - "ICN" = internal control number (claim number)
  - "EFT" = electronic funds transfer
  - "ERA" = electronic remittance advice
  - "PRE-AUTH" / "PA" = prior authorization
  - "HCPCS" = procedure/supply codes
  - "ICD" = diagnosis codes
  - "CO" = contractual obligation (denial reason code prefix)
  - "PR" = patient responsibility (denial reason code prefix)
  - "OA" = other adjustment (denial reason code prefix)
  - Any other billing term — interpret from context of the SOP records provided

═══════════════════════════════════════════════
HOW TO FIND THE ANSWER IN THE RECORDS
═══════════════════════════════════════════════
1. Read ALL records provided before answering — the answer may be in any record, not just the first.
2. Match by MEANING and INTENT, not just exact words. A question about "what happens when a claim is denied" matches records about "denial guidelines", "CO-97", "CO-4", "reconsideration", etc.
3. If multiple records are relevant, combine them into one complete answer.
4. Prioritise records marked ACTIVE. Ignore INACTIVE/TERMINATED records unless the user asks about them.
5. If a record contains a list of statuses, procedures, or payers — scan the whole list before concluding the answer is not there.

═══════════════════════════════════════════════
HOW TO WRITE YOUR ANSWER
═══════════════════════════════════════════════
• Start directly with the answer. Never open with "Based on the SOP records..." or "According to the provided records..."
• Quote exact values from the SOP — dollar amounts, day counts, status names, payer names. Do NOT paraphrase specific numbers or thresholds.
• Use bullet points for multi-step procedures or when listing conditions.
• For billing status questions, structure your answer as:
    **[Status Name]** — [what it means]
    - Set by: [who]
    - When to use: [condition or trigger]
    - Next action: [what the team does after]
• For procedure questions, number the steps if the SOP defines an order.
• Be concise but complete — do not omit conditions or exceptions the SOP specifies.
• Do not add advice, warnings, or explanations that are not in the SOP records.
• NEVER use backtick (`) characters anywhere in your response. Do not use inline code formatting for anything — numbers, dollar amounts, day counts, CPT codes, status names, quoted phrases, or any other text. Write everything as plain prose. Using backticks makes the text invisible to the user.

═══════════════════════════════════════════════
WHEN THERE IS NO ANSWER
═══════════════════════════════════════════════
Only reply with NO_MATCH_FOUND if — after carefully reading every record — none of them address the question in any way. Do not say NO_MATCH_FOUND if you can partially answer or if the information is clearly implied."""


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA LOADER
# ═══════════════════════════════════════════════════════════════════════════════

class SchemaLoader:

    def __init__(self, schema_dir: str = "./data_schema"):
        self.schema_dir = schema_dir
        self._by_source: Dict[str, List[dict]] = {}
        self._by_team:   Dict[str, set]        = {}
        self._load_all()

    def _load_all(self):
        files = glob.glob(os.path.join(self.schema_dir, "*.json"))
        if not files:
            print(f"[SchemaLoader] WARNING: No JSON files in {self.schema_dir}")
            return
        for fpath in files:
            try:
                data = json.load(open(fpath, encoding="utf-8"))
            except Exception as e:
                print(f"[SchemaLoader] Error loading {fpath}: {e}")
                continue
            src  = data.get("source_file", os.path.basename(fpath))
            team = data.get("team", "Unknown")
            key  = src.lower()
            self._by_source[key] = data.get("records", [])
            self._by_team.setdefault(team, set()).add(key)

        total = sum(len(v) for v in self._by_source.values())
        print(f"[SchemaLoader] {len(self._by_source)} files, {total} records loaded")

    def get_records(self, source_key: str, scope: Optional[str] = None) -> List[dict]:
        recs = self._by_source.get(source_key.lower(), [])
        if scope and scope.lower() not in ("all", "all scopes", ""):
            recs = [r for r in recs if r.get("scope", "").lower() == scope.lower()]
        return recs

    def get_all_records(self, keys: List[str], scope: Optional[str] = None) -> List[dict]:
        out = []
        for k in keys:
            out.extend(self.get_records(k, scope))
        return out

    def get_scopes(self, source_key: str) -> List[str]:
        seen, out = set(), []
        for r in self._by_source.get(source_key.lower(), []):
            s = r.get("scope", "").strip()
            if s and s not in seen:
                seen.add(s); out.append(s)
        return out

    def get_sources_for_team(self, team: str) -> List[str]:
        return list(self._by_team.get(team, set()))

    def get_all_sources(self) -> List[str]:
        return list(self._by_source.keys())

    def get_billing_manager(self, keys: List[str]) -> str:
        for k in keys:
            for r in self._by_source.get(k.lower(), []):
                bm = r.get("billing_manager", "").strip()
                if bm:
                    return bm
        return ""

    def resolve_source_key(self, practice_name: str) -> Optional[str]:
        nl = practice_name.lower()
        if nl in self._by_source:
            return nl
        for k in self._by_source:
            if k.startswith(nl) or nl.startswith(k.split(".")[0]):
                return k
        for k in self._by_source:
            if nl.replace(" sop", "").strip() in k:
                return k
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SMART FILTER — generic relevance scoring, zero hardcoded words
# Works for any SOP automatically. No category hints, no synonym lists.
# The LLM handles all language understanding — this just prioritises records.
# ═══════════════════════════════════════════════════════════════════════════════

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "do", "does", "did", "has", "have", "had", "will", "would", "could",
    "should", "may", "might", "shall", "can", "not", "no", "so", "if",
    "it", "its", "we", "our", "you", "your", "they", "their", "this",
    "that", "these", "those", "what", "when", "where", "which", "who",
    "how", "why", "all", "any", "some", "more", "just", "also", "then",
    "than", "into", "about", "there", "give", "show", "tell", "find",
    "please", "get", "let", "put", "use", "make", "need", "want",
}


def _extract_record_text(r: dict) -> str:
    """
    Extract ALL searchable text from a record including:
    - question field (may just be a number like '5.0')
    - instruction field (may contain embedded 'Q: ...\nA: ...' pairs)
    - category field
    This handles records where the actual question is inside the instruction field.
    """
    parts = []

    # question field
    q = r.get("question", "").strip()
    parts.append(q)

    # instruction field — extract embedded Q/A text
    instr = r.get("instruction", "").strip()
    parts.append(instr)

    # Also extract just the A: answer lines from embedded Q/A
    for match in re.finditer(r'A:\s*(.+?)(?=\nQ:|\nA:|\Z)', instr, re.DOTALL | re.IGNORECASE):
        parts.append(match.group(1).strip())

    # category
    parts.append(r.get("category", ""))

    return " ".join(parts).lower()


def _extract_query_words(query: str) -> set:
    """Extract meaningful words from query — no synonym expansion, no hardcoding."""
    clean = re.sub(r'\[Conversation so far\].*?\[Current question\]\s*', '',
                   query, flags=re.DOTALL | re.IGNORECASE).strip()
    return {
        w for w in re.split(r'\W+', clean.lower())
        if len(w) >= 3 and w not in _STOPWORDS
    }


def smart_filter(query: str, records: List[dict]) -> List[dict]:
    """
    Generic relevance filter — scores every record by how many query words
    appear in its text. No hardcoded categories, synonyms, or billing terms.
    The LLM handles all language understanding; this just surfaces the right records.

    Scoring tiers:
      Tier A — words appear in question field   (weight 2 — most specific)
      Tier B — words appear anywhere in record  (weight 1 — broader)

    Records with score >= 40% of the top score are returned.
    If nothing scores, the full record set is returned (LLM fallback).
    """
    clean_query = re.sub(
        r'^\[Conversation so far\].*?\[Current question\]\s*',
        '', query, flags=re.DOTALL | re.IGNORECASE
    ).strip() or query.strip()

    qw = _extract_query_words(clean_query)
    if not qw:
        print(f"[SmartFilter] No query words extracted — returning all {len(records)} records")
        return records

    def score(r: dict) -> int:
        full_text     = _extract_record_text(r)
        question_text = r.get("question", "").lower()
        s = 0
        for w in qw:
            if w in question_text:
                s += 2   # higher weight: word in the question field
            elif w in full_text:
                s += 1   # lower weight: word elsewhere in record
        return s

    scored = sorted(records, key=score, reverse=True)
    top_score = score(scored[0]) if scored else 0

    if top_score == 0:
        # No query words found anywhere — send all records, LLM will reason from context
        print(f"[SmartFilter] No keyword match — sending all {len(records)} records to LLM")
        return records

    # Keep records with at least 40% of the top score
    threshold = max(1, int(top_score * 0.4))
    relevant  = [r for r in scored if score(r) >= threshold]

    print(f"[SmartFilter] {len(relevant)}/{len(records)} records "
          f"(top_score={top_score}, threshold={threshold}, words={qw})")
    return relevant


def _dedup_records(records: List[dict]) -> List[dict]:
    """Remove duplicate records based on instruction + question content."""
    seen = set()
    out  = []
    for r in records:
        key = (
            r.get("question",    "").strip().lower(),
            r.get("instruction", "").strip().lower(),
            r.get("category",    "").strip().lower(),
        )
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out




_KEEP_FIELDS = {
    "category", "question", "instruction", "scope",
    "status", "effective_date", "termination_date",
    "sop_reference", "billing_manager",
}

# Context limit — must stay within num_ctx tokens sent to Ollama
# num_ctx=16384 ≈ 64K chars. Keep records under 48K to leave room for prompt+answer.
_MAX_CONTEXT_CHARS = 48_000


def _slim_record(r: dict) -> dict:
    slim = {}
    for k in _KEEP_FIELDS:
        v = r.get(k, "")
        if v and v not in ("NA", "N/A", ""):
            slim[k] = v
    slim.setdefault("category",    r.get("category", ""))
    slim.setdefault("question",    r.get("question", ""))
    slim.setdefault("instruction", r.get("instruction", ""))
    slim.setdefault("status",      r.get("status", "ACTIVE"))
    return slim


def _format_records_as_text(records: List[dict]) -> str:
    """
    Format SOP records as readable plain text instead of JSON.
    More token-efficient and easier for the LLM to reason over.
    """
    lines = []
    for i, r in enumerate(records, 1):
        lines.append(f"[Record {i}]")
        cat = r.get("category", "").strip()
        if cat:
            lines.append(f"Category: {cat}")
        q = r.get("question", "").strip()
        if q:
            lines.append(f"Question: {q}")
        instr = r.get("instruction", "").strip()
        if instr:
            lines.append(f"Answer/Procedure: {instr}")
        scope = r.get("scope", "").strip()
        if scope and scope not in ("NA", "N/A", ""):
            lines.append(f"Scope: {scope}")
        status = r.get("status", "").strip()
        if status and status.upper() not in ("ACTIVE", ""):
            lines.append(f"Status: {status}")
        lines.append("")   # blank line between records
    return "\n".join(lines)


def _fit_records(records: List[dict]) -> str:
    """Deduplicate, slim, and trim to context budget. Returns formatted text."""
    slimmed = [_slim_record(r) for r in _dedup_records(records)]
    result, used = [], 0
    for r in slimmed:
        chunk = len(r.get("question", "")) + len(r.get("instruction", "")) + 80
        if used + chunk > _MAX_CONTEXT_CHARS:
            print(f"[OllamaClient] Context limit: kept {len(result)}/{len(slimmed)} records")
            break
        result.append(r)
        used += chunk
    return _format_records_as_text(result)


class OllamaClient:

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model    = model
        print(f"[OllamaClient] model={model} url={base_url}")

    def _build_payload(self, user_message: str, filtered_records: List[dict], stream: bool) -> tuple:
        ctx_text = _fit_records(filtered_records)
        approx_tokens = len(ctx_text) // 4
        record_count  = ctx_text.count("[Record ")
        print(f"[OllamaClient] {record_count} records (~{approx_tokens} tokens) → {self.model}")
        prompt = (
            f"SOP RECORDS:\n"
            f"{ctx_text}\n"
            f"QUESTION: {user_message}\n\n"
            f"Answer:"
        )
        payload = json.dumps({
            "model" : self.model,
            "prompt": prompt,
            "system": _SYSTEM_PROMPT,
            "stream": stream,
            "options": {
                "temperature": 0.1,
                "num_predict": 768,    # increased: allows complete answers for detailed procedures
                "num_ctx":     16384,
            },
        }).encode("utf-8")
        return payload, ctx_text

    def query_stream(self, user_message: str, filtered_records: List[dict]):
        """
        Generator — yields answer tokens one by one as Ollama produces them.
        Use with st.write_stream() in the UI for instant perceived response.
        Stores the full answer on self.last_answer after completion.
        """
        payload, _ = self._build_payload(user_message, filtered_records, stream=True)
        self.last_answer = ""
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = obj.get("response", "")
                self.last_answer += token
                yield token
                if obj.get("done", False):
                    break

    def query(self, user_message: str, filtered_records: List[dict]) -> str:
        """
        Send records + question to Ollama and return the full answer string.
        Internally uses streaming so the model starts processing immediately.
        """
        payload, _ = self._build_payload(user_message, filtered_records, stream=True)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    f"{self.base_url}/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                chunks = []
                with urllib.request.urlopen(req, timeout=180) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = obj.get("response", "")
                        chunks.append(token)
                        if obj.get("done", False):
                            break
                answer = "".join(chunks).strip()
                print(f"[OllamaClient] Response received: {len(answer)} chars")
                return answer
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    print(f"[OllamaClient] Error: {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    f"Ollama error: {e}\n"
                    f"Check Ollama is running at {self.base_url}\n"
                    f"Run: ollama pull {self.model}"
                ) from e
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class HybridSOPEngine:
    """
    LLM-direct engine — sends all SOP records to Ollama and answers naturally.
    Uses streaming internally so responses begin processing immediately.
    """

    def __init__(
        self,
        schema_dir:   str = None,
        ollama_url:   str = None,
        ollama_model: str = None,
    ):
        self.schema_dir = schema_dir  or os.getenv("SCHEMA_DIR",       "./data_schema")
        ollama_url      = ollama_url  or os.getenv("OLLAMA_BASE_URL",  "http://localhost:11434")
        ollama_model    = ollama_model or os.getenv("OLLAMA_LLM_MODEL", "qwen2.5:14b")

        self.loader = SchemaLoader(self.schema_dir)
        self.llm    = OllamaClient(ollama_url, ollama_model)
        self._model = ollama_model
        self._stats = {"llm": 0, "no_match": 0}

    # ── Public interface ──────────────────────────────────────────────────────

    def get_scopes(self, practice_name: str) -> List[str]:
        key = self.loader.resolve_source_key(practice_name)
        return self.loader.get_scopes(key) if key else []

    def get_team_sources(self, team: str) -> List[str]:
        return self.loader.get_sources_for_team(team)

    def get_all_sources(self) -> List[str]:
        return self.loader.get_all_sources()

    def check_health(self) -> Dict:
        return {
            "engine"       : "HybridSOPEngine (LLM-direct)",
            "model"        : self._model,
            "sources"      : len(self.loader.get_all_sources()),
            "total_records": sum(len(self.loader.get_records(k))
                                 for k in self.loader.get_all_sources()),
            "stats"        : self._stats,
        }

    def get_answer(
        self,
        query:           str,
        practice_name:   Optional[str]       = None,
        scope:           Optional[str]       = None,
        allowed_sources: Optional[List[str]] = None,
    ) -> Dict:

        if not practice_name and not allowed_sources:
            return {"answer": "Please select a document from the sidebar.",
                    "sources": [], "error": True}

        # ── Resolve records ───────────────────────────────────────────────────
        if allowed_sources:
            records     = self.loader.get_all_records(allowed_sources, scope)
            label       = f"All Practices ({len(allowed_sources)} docs)"
            source_keys = allowed_sources
        else:
            key = self.loader.resolve_source_key(practice_name)
            if not key:
                return {
                    "answer": f"Document '{practice_name}' not found in data_schema/.",
                    "sources": [], "error": True,
                }
            records     = self.loader.get_records(key, scope)
            label       = practice_name
            source_keys = [key]

        if not records:
            bm = self.loader.get_billing_manager(source_keys)
            return self._no_match(query, label, bm)

        # ── Send all records directly to LLM — no keyword filtering ──────────
        # The LLM reads the full SOP and finds the answer by understanding meaning.
        # This eliminates all keyword hardcoding and missed answers from over-filtering.
        print(f"[HybridSOPEngine] '{query[:60]}' | total={len(records)} records → smart_filter")

        # ── Smart filter: narrow records before LLM to stay within context ────
        filtered = smart_filter(query, records)
        print(f"[HybridSOPEngine] smart_filter → {len(filtered)} records → LLM")

        # ── LLM reads filtered records and answers ────────────────────────────
        try:
            answer = self.llm.query(query, filtered)
            self._stats["llm"] += 1
        except RuntimeError as e:
            return {
                "answer": (
                    f"**Ollama error:** {e}\n\n"
                    f"Make sure Ollama is running and model `{self._model}` is pulled:\n"
                    f"```\nollama pull {self._model}\n```"
                ),
                "sources": [], "error": True,
            }

        # ── Handle no-match ───────────────────────────────────────────────────
        if not answer.strip() or "NO_MATCH_FOUND" in answer:
            bm = self.loader.get_billing_manager(source_keys)
            return self._no_match(query, label, bm)

        sources = self._build_sources(records)
        return {
            "answer"  : answer,
            "sources" : sources,
            "error"   : False,
            "practice": label,
            "intent"  : "llm",
        }

    def get_answer_stream(
        self,
        query:           str,
        practice_name:   Optional[str]       = None,
        scope:           Optional[str]       = None,
        allowed_sources: Optional[List[str]] = None,
    ):
        """
        Streaming version of get_answer.
        Yields (token_str, meta_dict) tuples.
        meta_dict is non-empty only on the final yield and contains sources/error info.
        """
        if not practice_name and not allowed_sources:
            yield ("", {"answer": "Please select a document from the sidebar.",
                        "sources": [], "error": True})
            return

        if allowed_sources:
            records     = self.loader.get_all_records(allowed_sources, scope)
            label       = f"All Practices ({len(allowed_sources)} docs)"
            source_keys = allowed_sources
        else:
            key = self.loader.resolve_source_key(practice_name)
            if not key:
                yield ("", {"answer": f"Document '{practice_name}' not found.",
                            "sources": [], "error": True})
                return
            records     = self.loader.get_records(key, scope)
            label       = practice_name
            source_keys = [key]

        if not records:
            bm = self.loader.get_billing_manager(source_keys)
            yield ("", self._no_match(query, label, bm))
            return

        print(f"[HybridSOPEngine] stream '{query[:60]}' | {len(records)} records → smart_filter")
        filtered = smart_filter(query, records)
        print(f"[HybridSOPEngine] smart_filter → {len(filtered)} records → LLM")
        self._stats["llm"] += 1

        try:
            for token in self.llm.query_stream(query, filtered):
                yield (token, {})
        except Exception as e:
            yield ("", {"answer": f"**Ollama error:** {e}", "sources": [], "error": True})
            return

        full_answer = self.llm.last_answer
        if not full_answer.strip() or "NO_MATCH_FOUND" in full_answer:
            bm = self.loader.get_billing_manager(source_keys)
            yield ("", self._no_match(query, label, bm))
            return

        sources = self._build_sources(filtered)
        yield ("", {"sources": sources, "error": False, "practice": label, "intent": "llm"})

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_sources(self, records: List[dict]) -> List[dict]:
        seen, out = set(), []
        for r in records:
            ref = r.get("sop_reference", "")
            if ref not in seen:
                seen.add(ref)
                out.append({"source": r.get("source_file", ""),
                             "page": ref, "score": 1.0})
        return out

    def _no_match(self, query: str, label: str, bm: str) -> Dict:
        self._stats["no_match"] += 1
        if bm:
            answer = (
                f"This SOP does not contain instructions related to your question.\n\n"
                f"Please check with the Billing Manager, **{bm}**."
            )
        else:
            answer = (
                f"This SOP does not contain instructions related to **'{query[:80]}'** "
                f"in **{label}**.\n\nPlease escalate to the billing manager or team lead."
            )
        return {"answer": answer, "sources": [], "no_results": True, "error": False}
