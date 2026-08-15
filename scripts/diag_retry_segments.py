#!/usr/bin/env python3
"""Retry 段级 trace 诊断 (deterministic, 不调用模型).

用法:
  python scripts/diag_retry_segments.py [output_dir] --done <done.csv> [--todo <todo.csv>]

对 done.csv 每行: 切分 semantic/protected 段, 逐段判定:
  segment type      (sem | prot)
  source phrase     该段原文
  resolved translation  (glossary直译 / cache命中译文 / 未resolved)
  source of resolution  (cache | glossary | model | protected | UNRESOLVED)
  required_translate    (sem 段=yes; prot 段=no)
  echo                  (resolved != '' 且 resolved==source=yes)

依赖: 复用 phase2b 的 split_semantic_spans / glossary_resolve / rebuild /
       materialize_from_cache, phase2a_catalog.norm_text, phrase_cache。
只读: 不写任何文件。输出到 stdout + 汇总统计。
"""
import sys, os, csv, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor" if False else ""))

# 复用 phase2b 内的段切分/术语/重建 (import 时不会触发 main)
import importlib.util
_spec = importlib.util.spec_from_file_location("pt2b", str(ROOT / "scripts" / "phase2b_translate.py"))
pt = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(pt)
except SystemExit:
    pass  # out_dir 缺省路径不存在时顶层不一定会 sys.exit; 保险吞掉
from phase2a_catalog import norm_text
from phrase_cache import PhraseCache, build_fingerprint

split_semantic_spans = pt.split_semantic_spans
glossary_resolve = pt.glossary_resolve
rebuild = pt.rebuild
normalize_model_output = pt.normalize_model_output
restore_protected = pt.restore_protected
title_creator_protection = pt.title_creator_protection


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default="output")
    ap.add_argument("--done", required=True, help="translation_done_*.csv (retry 输出)")
    ap.add_argument("--todo", default=None, help="可选 todo/manifest, 取 decision='TRANSLATE' 判 required")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    if not out_dir.is_dir():
        out_dir = ROOT / "output"

    done_p = Path(args.done)
    if not done_p.exists():
        done_p = out_dir / args.done
    if not done_p.exists():
        print(f"[HARD-FAIL] done 文件不存在: {done_p}", file=sys.stderr)
        return 3

    auth_tids = set()
    todo_p = Path(args.todo) if args.todo else None
    if todo_p and not todo_p.exists():
        todo_p = out_dir / args.todo
    if todo_p and todo_p.exists():
        with open(todo_p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("decision") or "").strip() in ("TRANSLATE", "FULL_TRANSLATE", "PARTIAL_TRANSLATE"):
                    auth_tids.add((r.get("translation_id") or "").strip())
    else:
        # 无 todo: 依 done 里 decision 字段
        with open(done_p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("decision") or "").strip() in ("TRANSLATE", "FULL_TRANSLATE", "PARTIAL_TRANSLATE"):
                    auth_tids.add((r.get("translation_id") or "").strip())

    cache = PhraseCache(out_dir, model=None)
    rows = list(csv.DictReader(open(done_p, encoding="utf-8-sig")))

    # 汇总计数
    n_rows = len(rows)
    n_echo_cache_phrases = 0      # cache 里 == source 的 semantic phrase 数
    n_echo_rows = 0               # 出现 ≥1 个 echo semantic phrase 的行
    n_partial_untrans = 0         # 有 未resolved 或 echo semantic 段的行 (partial/untranslated)
    rows_affected = set()
    n_echo_rows_set = set()

    print("tid\trow_status\tseg_idx\tseg_type\tsource_phrase\tresolved\tresolution\trequired_translate\techo\tprotected\treason")
    for r in rows:
        tid = (r.get("translation_id") or "").strip()
        text = norm_text(r.get("source_text") or "")
        status = (r.get("status") or "").strip()
        mode = (r.get("translate_mode") or "").strip()
        translation = (r.get("translation") or "").strip()
        auth = tid in auth_tids

        # BUG4: 应用与 translator 同源的 TITLE creator/identifier 保护, 再判 required_translate。
        prot_spans, prot_reasons = title_creator_protection(text)
        segs, _ = split_semantic_spans(text, force_prot_spans=prot_spans)
        gloss, pending = glossary_resolve(segs)
        pending = [p for p in pending if p["t"].strip()]
        ctx = []  # 诊断无 ctx (与 cache 写入时一致; 有 ctx 的 fingerprint 查不到属正常差异)
        resolved = dict(gloss)
        cache_map = {}   # key -> 是否 cache 命中 (诊断 source-of-resolution)
        # 查 cache 对每个 pending
        for p in pending:
            fp = build_fingerprint(source_phrase=p["t"].strip(),
                                   glossary_hint=p.get("gloss_hint", ""),
                                   context=p.get("ctx", ""))
            hit = cache.get(fp)
            if hit:
                cache_map[p["key"]] = True
                cand = normalize_model_output(hit["translation"])
                if cand.strip() and cand.strip() == p["t"].strip():
                    n_echo_cache_phrases += 1
                    n_echo_rows_set.add(tid)
                resolved[p["key"]] = cand
            else:
                cache_map[p["key"]] = False
            # else: 保留 UNRESOLVED (resolved 无该 key)
        # 逐段 trace
        seg_idx = 0
        for si, s in enumerate(segs):
            if s["kind"] == "prot":
                # prot 段: 区分"creator/identifier 保护"与"通用格式保护"
                _reason = ""
                _protected = "no"
                _pt = s["t"].strip()
            if s["kind"] == "prot":
                # prot 段: 区分"creator/identifier 保护"与"通用格式保护"
                _reason = ""
                _protected = "no"
                _pt = s["t"].strip()
                for span, why in prot_reasons.items():
                    _sub = norm_text(text)[span[0]:span[1]].strip()
                    if span[0] == 0 or (_sub and (_sub == _pt or _pt == _sub or
                                                  _pt.startswith(_sub) or _sub.startswith(_pt))):
                        _protected = "yes"
                        _reason = why
                print(f"{tid}\t{status}\t{si}\tprot\t{s['t']}\t{s['t']}\tprotected\tno\tno\t{_protected}\t{_reason}")
                continue
            # sem
            seg_idx += 1
            src = s["t"].strip()
            rv = resolved.get(s.get("key"))
            req = "yes" if s.get("key") in [q.get("key") for q in pending] else "no"
            protected = "no"
            reason = ""
            if req == "yes":
                if rv is None or not str(rv).strip():
                    res_src = "UNRESOLVED"
                    resolved_txt = ""
                    n_partial_untrans += 1
                    rows_affected.add(tid)
                else:
                    resolved_txt = str(rv).strip()
                    if s.get("key") in gloss:
                        res_src = "glossary"
                    elif cache_map.get(s.get("key")):
                        res_src = "cache"   # fingerprint 在 cache 里
                    else:
                        res_src = "model"   # 无 cache -> 本轮模型翻译
                    # echo?
                    if resolved_txt == src:
                        n_partial_untrans += 1
                        rows_affected.add(tid)
                        n_echo_rows_set.add(tid)
                echo = "yes" if (resolved_txt and resolved_txt == src) else "no"
                print(f"{tid}\t{status}\t{si}\tsem\t{src}\t{resolved_txt}\t{res_src}\tyes\t{echo}\t{protected}\t{reason}")
            else:
                # glossary 直译 (零模型) 的 sem 段 -> required_translate=no
                resolved_txt = str(rv).strip() if rv is not None else ""
                echo = "yes" if (resolved_txt and resolved_txt == src) else "no"
                print(f"{tid}\t{status}\t{si}\tsem\t{src}\t{resolved_txt}\tglossary\tno\t{echo}\t{protected}\t{reason}")

    n_echo_rows = len(n_echo_rows_set)
    print("\n===== 汇总 =====")
    print(f"rows = {n_rows}")
    print(f"cached semantic echo count  = {n_echo_cache_phrases}")
    print(f"rows affected (≥1 echoed semantic seg, from cache or resolved==src) = {n_echo_rows}")
    print(f"partial untranslated semantic segment rows = {len(rows_affected)}")
    cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
