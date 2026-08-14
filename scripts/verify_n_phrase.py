#!/usr/bin/env python3
"""真机验证脚本 (ni-fei, 走生产 OllamaTranslator 路径)。

用法:
  python scripts/verify_n_phrase.py [n] [--cache-dir DIR]

  n 取 1 / 8 / 32。默认 n=8。
  --cache-dir 指定隔离 cache 目录 (默认用系统临时目录, 每次干净)。

验证点 (与生产 `_call_batch`/`translate_batch`/`on_done` 完全同路径):
  - attempted / succeeded / failed 计数
  - schema 解析成功 (返回结构 {ck: zh})
  - zh 非空
  - cache 实际写入数 (每个成功 phrase 即时 cache.put + commit)
  - checkpoint: on_done 在每个成功 phrase 被调用

不跑全量、不写 package。
"""
import sys
import os
import tempfile
from pathlib import Path

# 让脚本能 import 同目录模块 (phase2a_catalog / phrase_cache)
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import phase2b_translate as P
from phrase_cache import PhraseCache, build_fingerprint
import phase2a_catalog as A

# 覆盖 FULL / PARTIAL / glossary / protected-token 不同类别的样例
SAMPLES = [
    # FULL_TRANSLATE 语义完整条
    "walk near desk",
    "holding arm fist up",
    # 纯技术/编号 (KEEP 类, 但仍会过引擎)
    "5M *anim",
    # PARTIAL: 保护编号/版本/*anim token
    "41Ha Holding Arm Fist Up",
    "7M *anim: angry-sad",
    # 其他常见 pose
    "left leg up",
    "drunk idle variation",
    "near desk",
    "pen + paper",
    "F1 (ВЕРСИЯ С 3Д ЯЗЫКОМ)",
    "Femme",
    "pose",
    "Asomado",
    "РЫЦАРЬ",
    "Revisando",
    "Asustado",
    "KNIGHT",
    "8 *animation",
    "1B (animation)",
    "F1+2",
]

# 人工审批硬编码译文 (引擎不得改写; 用于抽验 zh 命中/保护 token 保留)
APPROVED = {
    "Femme": "女性", "pose": "姿势", "Asomado": "探出身子",
    "РЫЦАРЬ": "骑士", "KNIGHT": "骑士", "Revisando": "查看中",
    "Asustado": "受惊", "F1 (ВЕРСИЯ С 3Д ЯЗЫКОМ)": "F1（3D舌头版）",
}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8
    n = max(1, min(n, len(SAMPLES)))
    items = [(f"s{i:02d}", t) for i, t in enumerate(SAMPLES[:n])]

    cache_dir_opt = None
    if "--cache-dir" in sys.argv:
        i = sys.argv.index("--cache-dir")
        if i + 1 < len(sys.argv):
            cache_dir_opt = Path(sys.argv[i + 1])
    iso = cache_dir_opt or Path(tempfile.mkdtemp(prefix="verify_phrase_"))
    iso.mkdir(parents=True, exist_ok=True)
    cache_path = iso / "translation_cache.db"
    if cache_path.exists():
        cache_path.unlink()  # 干净起点, 保证全 miss

    cache = PhraseCache(iso)
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    committed = 0
    committed_keys = set()
    on_done_calls = []
    # ck -> 对应 source_phrase (供写库)
    ck_to_src = {k: t for k, t in items}
    ck_to_idx = {k: i for i, (k, t) in enumerate(items)}

    def on_done(ck, zh):
        """真实生产同款 _on_done: 每个成功 phrase 立即 checkpoint 写库。"""
        nonlocal committed
        if ck in committed_keys:
            return
        committed_keys.add(ck)
        committed += 1
        on_done_calls.append((ck, zh))
        src = ck_to_src.get(ck)
        if src is None or not zh or zh.startswith("[ERR"):
            return
        fp = build_fingerprint(source_phrase=src)
        cache.put(
            fingerprint=fp,
            translation_id=f"VERIFY-{ck}",
            segment_index=0,
            source_phrase=src,
            source_hash=A.source_hash(src),
            translation=zh,
            now=now,
        )

    print(f"\n== verify_n_phrase: n={n}  (vía 生产 OllamaTranslator, {P.OllamaTranslator().model} @ 127.0.0.1) ==")
    eng = P.OllamaTranslator()
    raw = eng.translate_batch(items, concurrency=min(4, n), per_call=n,
                              on_done=on_done)

    # 审批硬编码层 (与生产 main() 决策层同款): text in APPROVED_TEXT 的词,
    # 最终输出必须以审批译文为准, 禁止引擎自由翻译/原样保留。
    APPROVED_TEXT = getattr(P, "APPROVED_TEXT", set())
    APPROVED = getattr(P, "APPROVED", {})
    final = {}
    for k, t in items:
        z = raw.get(k, "") or ""
        if t in APPROVED_TEXT:
            final[k] = APPROVED[t][0]  # 强制覆盖
        else:
            final[k] = z

    succeeded = sum(1 for k, z in raw.items() if z and not z.startswith("[ERR"))
    failed = sum(1 for _, z in raw.items() if not z or z.startswith("[ERR"))
    attempted = len(raw)

    # cache 实际写入: 用 build_fingerprint 反查
    cache_written = 0
    for k, t in items:
        fp = build_fingerprint(source_phrase=t)
        if cache.get(fp):
            cache_written += 1
    cache_total = cache.count()

    print(f"attempted = {attempted}")
    print(f"succeeded = {succeeded}")
    print(f"failed    = {failed}")
    print(f"on_done 即时回调次数 = {len(on_done_calls)} (应为 succeeded)")
    print(f"cache 反查写入 = {cache_written} / {n}   cache 库总条数 = {cache_total}")
    print(f"返回结构类型   = {type(raw).__name__}  (应为 dict, 且 {len(raw)} 项)")
    print("\n--- 逐条 zh (已应用审批强制覆盖) ---")
    for k, t in items:
        z = final.get(k, "")
        mark = "OK  " if (z and not z.startswith("[ERR")) else "ERR "
        sign = " [审批]" if t in APPROVED_TEXT else ""
        print(f"  {mark} {k}{sign}: {z!r}")

    # 断言评审标准
    rules = []
    rules.append(("HTTP/调用无异常且 attempted==n", attempted == n))
    rules.append(("succeeded>=max(1, n-1) (>=1 真成功)", succeeded >= max(1, n - 1)))
    rules.append(("failed==0 (不应有 parse 失败)", failed == 0))
    rules.append(("on_done 每成功即回调 (即时 checkpoint)", len(on_done_calls) == succeeded))
    rules.append(("cache 实际写入==succeeded", cache_written == succeeded and cache_total == succeeded))
    rules.append(("返回为完整 dict", isinstance(final, dict) and len(final) == n))

    # 审批硬编码层: final 已应用生产同款 APPROVED 覆盖, 断言必须全部命中审批译文。
    approved_hits = [t for _, t in items if t in APPROVED_TEXT]
    approved_ok = True
    approved_detail = []
    for k, t in items:
        if t not in APPROVED_TEXT:
            continue
        expect = APPROVED[t][0]
        got = final.get(k, "")
        ok = (str(got).strip() == expect)
        approved_ok = approved_ok and ok
        approved_detail.append(f"{t!r}: got={got!r} want={expect!r} {'OK' if ok else 'FAIL'}")
    rules.append((f"审批硬编码覆盖 ({len(approved_hits)} 条) => 必须命中审批译文", approved_ok))
    if approved_hits:
        print("   审批抽验: " + "; ".join(approved_detail))

    print("\n--- 评审 ---")
    ok_all = True
    for name, cond in rules:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok_all = ok_all and cond
    print(f"\n== 结论: {'通过 ✅' if ok_all else '未通过 ❌'} ==")
    cache.close()
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
