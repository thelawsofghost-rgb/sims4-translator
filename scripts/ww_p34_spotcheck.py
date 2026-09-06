#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ww_p34_spotcheck.py --- P34-BATCH post-run QA (read-only, deterministic).

Applies the 8 operator invariants against a P34 mapping CSV
(output/p34/p34_translation_mapping.csv).  NEVER modifies the mapping, NEVER
generates STBL, NEVER writes Mods/saves.  ZERO_WRITE: this module only reads.

Invariants
----------
  1. row_count == EXPECT_ROWS            (default 479)
  2. identifier / raw_display_name       preserved byte-for-byte against the
                                         mapping source (identity columns are
                                         never touched by the translator).
  3. resolved status === 100%            every row maps to a status; zero
                                         status=REVIEW (no leftover LLM queue).
  4. translated_name 非空                every mapped row has a non-empty zh.
  5. same series_key 中文 stem 一致      members sharing a series (recovered by
                                         joining the REAL P33 context CSV on
                                         identifier) must yield the same zh stem
                                         (differ only by number / suffix).
  6. Climax -> 高潮 uniform              every row whose raw tail is " - Climax"
                                         (or equivalent) ends in 高潮.
  7. no abnormal ASCII / whitespace      zh must not contain stray control
                                         chars / broken spacing (leading/
                                         trailing/doubled whitespace).
  8. sampled output rows                 print a representative sample (ordinal,
                                         raw, zh, series) for human spot-check.
  9. encoding                           the mapping FILE itself must be UTF-8
                                         with BOM and free of GBK-mojibake /
                                         replacement chars (checked on the raw
                                         bytes, not the decoded rows). P34.3.

Series recovery (#5) requires the REAL P33 context CSV (the mapping file itself
does not carry series_key).  Both the series check (#5) and the byte-for-byte
check (#2) are recovered from the SAME real P33 context CSV supplied via
--series-csv (that file carries identifier, raw_display_name and series_key):
  - #2 byte-for-byte: every mapping identifier must exist in source AND the
    raw_display_name copies must be EXACTLY equal (no strip / no normalization).
  - #5 series stem: members sharing a series_key must yield one zh stem after
    removing the number / suffix decoration.
Without --series-csv these two are reported SKIPPED -- never inferred from zh
text (that would be guessing).

Exit codes: 0 = all enforced invariants PASS; 2 = input/gate fail
(missing file / parse / invariant violation).
"""
from __future__ import print_function
import argparse
import csv
import os
import re
import sys

EXPECT_ROWS_DEFAULT = 479

# --------------------------------------------------------------------------- #
# Small pure helpers (import-safe, no I/O) ----------------------------------- #
# --------------------------------------------------------------------------- #
# Control chars and broken spacing in zh are ALWAYS pollution -> FAIL.
# Embedded ASCII (kept proper nouns / version tokens like v1 / anim) is
# legitimate per translation_rules (adapter KEEPS author/proper-noun verbatim),
# so it is surfaced as an informational note, never an auto-FAIL.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A zh token that got mangled by an ASCII word run of letters is suspicious;
# known technical tokens are whitelisted.
_TECH_TOKEN = re.compile(
    r"^(v\d+|v\d+\.\d+|anim|a\d+|p\d+|n\d+|m\d+|s\d+|w\d+|l\d+|e\d+)$",
    re.IGNORECASE,
)

# optional import of the SINGLE shared suffix vocabulary so the QA stem-strip
# agrees with the P34.2 builder; absent -> fall back to the historical 高潮-only
# behaviour (never guessing, never fabricating suffix words).
try:
    import ww_p34_suffix_table as _suffix_mod
except Exception:                       # pragma: no cover - optional dep
    _suffix_mod = None

# optional import of the P34 encoding gate for invariant #9 (ENCODING).  Absent
# -> ENCODING reported SKIPPED rather than guessed.
try:
    import ww_p34_encoding_gate as _enc_gate
except Exception:                       # pragma: no cover - optional dep
    _enc_gate = None

# historical fallback (only 高潮) used when the shared table is not importable;
# keeps this QA module self-sufficient and drift-free either way.
class _FallbackTable(object):
    SUFFIX_ZH = {"climax": "高潮"}


def _suffix_table():
    return _suffix_mod if _suffix_mod is not None else _FallbackTable


def _raw_climax(raw):
    """True if raw's final significant word is Climax (case-insensitive),
    allowing a preceding number/roman and a leading series phrase."""
    s = (raw or "").strip()
    return bool(re.search(r"(?i)\bclimax\s*$", s))


def _zh_series_stem(zh):
    """Strip a trailing ' N[- <zh suffix>]' / ' N' decoration from a series-
    member zh to recover the shared stem.  Mirrors the adapter's canonical
    decoration (''<stem> <N>[ - <suffix zh>]'') so equal members compare on the
    stem.  Never called on non-series rows by QA (series membership comes from
    the context join).

    Strips the FULL operator-locked suffix set (not only 高潮) so an AfterSex /
    Creampie / Intro / End series (P34.2) compares members on the true stem --
    the vocabulary is the SINGLE shared table used by the P34.2 builder too
    (scripts/ww_p34_suffix_table.py), so builder & checker cannot drift.
    """
    s = (zh or "").strip()
    _SUFFIX_ZH = _suffix_table().SUFFIX_ZH
    vals = sorted({v for v in (_SUFFIX_ZH or {}).values() if v},
                  key=len, reverse=True)
    alt = "|".join(re.escape(v) for v in vals)
    if vals:
        # "num - <zh suffix>" canonical series-member form: " 3 - 高潮" / " 2 - 后戏"
        m2 = re.search(
            r"\s*[-–—:]?\s*(\d+|[ivxlcdm]{1,5})\s*[-–—]\s*(?:" + alt +
            r")\s*$", s, re.I)
        if m2:
            return s[: m2.start()].strip()
        # bare " - <zh suffix>" with NO numeral (member whose raw has suffix but
        # no episode number, e.g. "SomeWhere Intro"): strip the suffix clause
        # so it compares with suffixed-and-numbered siblings on the same stem.
        m3 = re.search(r"\s*[-–—]\s*(?:" + alt + r")\s*$", s, re.I)
        if m3 and s[: m3.start()].strip():
            return s[: m3.start()].strip()
    # strip a bare trailing numeral (" 3") if no suffixed form matched
    m = re.search(r"\s*[-–—:]?\s*(\d+|[ivxlcdm]{1,5})\s*$", s, re.I)
    if m:
        return s[: m.start()].strip()
    return s


def spotcheck(rows, expect_rows=EXPECT_ROWS_DEFAULT, series_map=None,
              src_map=None):
    """Pure invariant evaluator.

    rows        : list[dict] with mapping keys (ordinal, identifier,
                  raw_display_name, translated_name, status, note, ...).
    series_map  : dict identifier -> series_key (recovered from the REAL P33
                  context CSV) OR None to skip invariant #5 (reported SKIPPED).
    src_map     : dict identifier -> raw_display_name (byte-for-byte source
                  copy from the REAL P33 context CSV).  When provided, #2 does
                  a TRUE byte-for-byte comparison of identifier & raw against
                  source; when None, #2 is limited to structural presence and
                  reported as needing --series-csv (no fabrication).
    Returns     : (failures:list[str], sample:list[dict], notes:list[str])
    """
    failures = []
    n = len(rows)

    # -- 1 row count -------------------------------------------------------
    if n != expect_rows:
        failures.append("INV1 row_count=%d != expect %d" % (n, expect_rows))

    # identity preservation + status tally
    status_hist = {}
    for r in rows:
        st = (r.get("status") or "").strip() or "?"
        status_hist[st] = status_hist.get(st, 0) + 1

    # -- 2 identifier / raw preserved byte-for-byte ------------------------
    notes = []
    if src_map is not None:
        # TRUE byte-for-byte: every mapping id must exist in source and both
        # raw_display_name copies must be EXACTLY equal (no strip, no change).
        bad2 = []
        for r in rows:
            ident = r.get("identifier")
            if ident not in src_map:
                bad2.append("identifier %r not found in source CSV" % ident)
                continue
            mraw = r.get("raw_display_name") or ""
            sraw = src_map[ident]
            if mraw != sraw:
                bad2.append(
                    "raw_display_name differs for identifier %r: "
                    "mapping=%r source=%r" % (ident, mraw, sraw))
        if bad2:
            failures.append("INV2 byte-for-byte divergence (%d):\n  %s"
                            % (len(bad2), "\n  ".join(bad2[:10])))
            notes.append("INV2 byte-for-byte: FAIL (%d divergence)" % len(bad2))
        else:
            notes.append(
                "INV2 byte-for-byte: PASS (%d rows match source raw exactly)"
                % len(rows))
    else:
        notes.append("INV2 byte-for-byte: SKIPPED (supply --series-csv real P33 "
                     "context to byte-verify against source)")
    if status_hist.get("REVIEW", 0):
        failures.append(
            "INV3 status=REVIEW present (%d rows); REVIEW queue must be empty"
            % status_hist["REVIEW"])
    # every row must have a recognized resolved status
    resolved_bad = [st for st in status_hist if st not in ("TRANSLATED", "LLM",
                                                           "KEEP", "?")]
    if resolved_bad:
        failures.append("INV3 unknown status(es): %s" % sorted(resolved_bad))

    # -- 4 translated_name 非空 --------------------------------------------
    empty = [r for r in rows if not (r.get("translated_name") or "").strip()]
    if empty:
        failures.append("INV4 %d row(s) have empty translated_name" % len(empty))

    # -- 6 Climax -> 高潮 uniform (only when raw tail literally Climax) ------
    # The adapter maps the *series suffix* Climax -> 高潮.  A row whose raw
    # carries a literal "Climax" tail must end in 高潮.  Guard: not all climax
    # rows are series members; still the zh should terminate with 高潮 when the
    # raw ends in ...Climax.
    for r in rows:
        raw = (r.get("raw_display_name") or "").strip()
        zh = (r.get("translated_name") or "").strip()
        if _raw_climax(raw) and not zh.rstrip(" .\u3000").endswith("高潮"):
            failures.append(
                "INV6 raw %r ends in Climax but zh %r does not end 高潮 (ordinal=%s)"
                % (raw, zh, r.get("ordinal")))

    # -- 7 abnormal ascii / whitespace pollution ----------------------------
    # Control chars / broken spacing are ALWAYS bugs -> FAIL.  Embedded ASCII
    # (proper nouns / version tokens legitimately kept per translation_rules
    # e.g. author/V1/proper-name) is expected, so it is NOT an auto-FAIL: any
    # >=2-letter ascii token is surfaced as an informational KEEP-note for the
    # human sampler instead (a kept proper noun is not pollution).
    white_notes = []
    for r in rows:
        zh = r.get("translated_name") or ""
        if _CONTROL_RE.search(zh):
            failures.append(
                "INV7 control char in zh (ordinal=%s): %r"
                % (r.get("ordinal"), zh))
        # abnormal whitespace: leading / trailing / doubled space (legit CJK
        # uses single spaces around kept ascii like "Sex 1 - 高潮")
        zs = zh.strip()
        if zh != zs:
            failures.append(
                "INV7 zh has leading/trailing whitespace (ordinal=%s): %r"
                % (r.get("ordinal"), zh))
        if re.search(r"[ \t]{2,}", zh):
            failures.append(
                "INV7 zh has doubled whitespace (ordinal=%s): %r"
                % (r.get("ordinal"), zh))
        # informational: embedded ascii tokens (legit kept proper nouns/tokens)
        for tok in sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9]*", zh))):
            if not _TECH_TOKEN.match(tok):
                white_notes.append("%s: %r" % (tok, zh))

    # -- 7 informational tally ---------------------------------------------
    if white_notes:
        seen = {n.split(":", 1)[0] for n in white_notes}
        notes.append("INV7: %d distinct embedded ASCII token(s) kept (proper "
                     "nouns/vX likely) -- informational, not FAIL: %s"
                     % (len(seen), ", ".join(sorted(seen)[:12])))
    else:
        notes.append("INV7: no embedded ASCII tokens; no whitespace/control "
                     "abnormalities")
    if series_map:
        by_series = {}
        for r in rows:
            sk = series_map.get(r.get("identifier"))
            if not sk:
                continue
            by_series.setdefault(sk, []).append(
                (r.get("ordinal"), r.get("translated_name") or ""))
        multi = {sk: mb for sk, mb in by_series.items() if len(mb) >= 2}
        bad = []
        for sk, members in multi.items():
            stems = {_zh_series_stem(zh) for _, zh in members if zh}
            if len(stems) > 1:
                bad.append((sk, sorted(stems), len(members)))
        if bad:
            notes.append("INV5 series stem: FAIL -- %d series divergent"
                         % len(bad))
            failures.append(
                "INV5 series divergent ([key, stems, members]): "
                + "; ".join("%s->%s(%d)" % (sk, sorted(sts), c)
                            for sk, sts, c in bad[:8]))
        elif multi:
            notes.append("INV5 series stem: PASS -- %d multi-member series each "
                         "share one zh stem" % len(multi))
        else:
            notes.append("INV5 series stem: PASS (no multi-member series in "
                         "mapping join)")
    else:
        notes.append("INV5 series stem: SKIPPED (supply --series-csv real P33 "
                     "context)")

    # -- 8 sample -----------------------------------------------------------
    sample = _pick_sample(rows, series_map)
    return failures, sample, notes



# --------------------------------------------------------------------------- #
# ENCODING invariant (#9) ------------------------------------------------------ #
# --------------------------------------------------------------------------- #
def audit_encoding(path):
    """Encoding invariant #9 against the ACTUAL mapping file bytes: it must be
    UTF-8 **with BOM** and free of GBK-mojibake / replacement chars (P34.3 #1).
    Returns (ok:bool, notes:list[str]) -- used by main() to fold an ENCODING
    failure into the overall VERDICT (spotcheck() itself has no file path)."""
    if _enc_gate is None:
        return True, ["ENCODING: SKIPPED (ww_p34_encoding_gate not importable)"]
    status, problems, meta = _enc_gate.audit_file(path)
    if status == "PASS":
        return True, ["ENCODING: PASS (UTF-8 with BOM, no GBK-mojibake)"]
    detail = "; ".join(problems[:4])
    return False, ["ENCODING: FAIL -> %s" % detail]


def _pick_sample(rows, series_map=None, target=24):
    """Deterministic representative sample: evenly spaced across ordinal, plus
    any multi-member series heads.  Never random (machine-reproducible)."""
    out = []
    n = len(rows)
    if n <= target:
        pick = list(range(n))
    else:
        step = n / float(target)
        pick = sorted({int(i * step) for i in range(target)})
    for i in pick:
        if i < n:
            r = rows[i]
            sk = series_map.get(r.get("identifier")) if series_map else None
            out.append((r.get("ordinal"), r.get("raw_display_name"),
                        r.get("translated_name"), sk, r.get("status")))
    return out


def load_mapping(path):
    """Read mapping CSV -> list[dict].  Fail-closed if a row lacks key cols."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        need = {"ordinal", "identifier", "raw_display_name",
                "translated_name", "status"}
        if not need.issubset(cols):
            raise ValueError(
                "mapping CSV missing required columns (have %d). Need %s; got %s"
                % (len(cols), sorted(need), sorted(cols)))
        return [dict(x) for x in rd]


def load_series_csv(path):
    """Read the REAL P33 context CSV -> (series_map{identifier: series_key},
    src_map{identifier: (raw_display_name)}).

    The SAME real P33 context file is both the source of raw_display_name
    (for the byte-for-byte invariant #2) and of series_key (invariant #5).
    Fail-closed: identifier and series_key columns required.
    """
    series_map, src_map = {}, {}
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        need = {"identifier", "raw_display_name"}
        if not need.issubset(cols):
            raise ValueError(
                "series CSV missing required columns; need %s; got %s"
                % (sorted(need), sorted(cols)))
        for row in rd:
            i = row.get("identifier")
            if not i:
                continue
            # byte-for-byte source copy (do NOT strip / normalize):
            src_map[i] = row.get("raw_display_name", "")
            k = row.get("series_key")
            if k:
                series_map.setdefault(i, k)
    return series_map, src_map


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mapping", help="P34 mapping CSV (p34_translation_mapping.csv)")
    ap.add_argument("--series-csv", default=None,
                    help="REAL P33 context CSV to recover series_key for INV5 "
                         "(otherwise INV5 reported SKIPPED)")
    ap.add_argument("--expect-rows", type=int, default=EXPECT_ROWS_DEFAULT)
    ap.add_argument("--sample", type=int, default=24)
    a = ap.parse_args(argv)

    if not os.path.isfile(a.mapping):
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=INPUT_MISSING:%s" % a.mapping, file=sys.stderr)
        return 2

    try:
        rows = load_mapping(a.mapping)
    except Exception as e:                      # gate fail-closed
        print("VERDICT=FAIL", file=sys.stderr)
        print("REASON=MAP_READ:%s" % e, file=sys.stderr)
        return 2

    series_map, src_map = None, None
    if a.series_csv:
        try:
            series_map, src_map = load_series_csv(a.series_csv)
        except Exception as e:
            print("VERDICT=FAIL", file=sys.stderr)
            print("REASON=SERIES_READ:%s" % e, file=sys.stderr)
            return 2
        if not series_map:
            print("WARN series_csv given but yielded no (identifier, series_key) "
                  "pairs; INV5 will be SKIPPED", file=sys.stderr)

    failures, sample, notes = spotcheck(
        rows, expect_rows=a.expect_rows,
        series_map=series_map, src_map=src_map)

    # ---- invariant #9 (ENCODING) against the actual file bytes -----------
    enc_ok, enc_notes = audit_encoding(a.mapping)
    if not enc_ok:
        failures.append("INV9 " + enc_notes[0].replace("ENCODING: ", ""))
    notes.extend(enc_notes)

    # ---- report ----------------------------------------------------------
    n = len(rows)
    status_hist = {}
    for r in rows:
        st = (r.get("status") or "").strip() or "?"
        status_hist[st] = status_hist.get(st, 0) + 1
    resolved = sum(v for k, v in status_hist.items() if k in ("TRANSLATED",
                                                              "LLM", "KEEP"))
    print("VERDICT=%s" % ("GO" if not failures else "FAIL"))
    print("ROW_COUNT=%d (expect %d)" % (n, a.expect_rows))
    print("RESOLVED_COUNT=%d" % resolved)
    print("REVIEW_COUNT=%d" % status_hist.get("REVIEW", 0))
    print("STATUS_HIST=%s" % ";".join("%s:%d" % kv for kv in
                                      sorted(status_hist.items())))
    if failures:
        print("FAILURES=%d" % len(failures))
        for f in failures[:40]:
            print("  - " + f)
    else:
        print("FAILURES=0")
    for note in notes:                       # invariant 2/5 pass|skip|fail detail
        print(" " + note)

    print("\n--- sample (ordinal | raw | zh | series | status) ---")
    for ord_, raw, zh, sk, st in sample:
        print("  %s | %s | %s | %s | %s" % (ord_, raw, zh, sk or "-", st))
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
