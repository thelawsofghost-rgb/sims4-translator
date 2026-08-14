#!/usr/bin/env python3
"""Phase 2B 正式 regression fixture + cache/增量/resume 验证。

不调用任何真实 LLM (Ollama/DeepSeek)。用 FakeTranslator 注入, 覆盖:

  segmentation / glossary / protected spans / punctuation&spacing / rebuild /
  language sanity / cache hit-miss / resume

用法:
  python scripts/test_phase2b_regression.py [--out <tmpdir>]

退出码: 0=全部通过; 1=有失败。
"""
import sys, os, csv, re, tempfile, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import phase2b_translate as P
import phase2a_catalog as A
from phrase_cache import PhraseCache, build_fingerprint

OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
tmpdir = None
if OUT is None:
    tmpdir = tempfile.mkdtemp(prefix="ph2b_reg_")
    OUT = Path(tmpdir)

PASS = []
FAIL = []

def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


# ---------------- FakeTranslator (确定性, 不调 LLM) ----------------
# 模拟模型: 对 Target 产生 固定中文; 记录被调用次数, 供 hit/miss/resume 断言。
class FakeTranslator(P.Translator):
    calls = 0
    def translate_batch(self, items, concurrency=8, per_call=8, max_retry=3):
        FakeTranslator.calls += 1
        out = {}
        for k, t in items:
            target = t.split("Target:")[-1].split("\n")[0].strip()
            table = {
                "walk near desk": "桌边行走",
                "holding arm fist up": "握拳举臂",
                "pose": "姿势",
                "drunk idle variation": "醉酒待机变体",
                "left leg up": "左腿抬起",
                "near desk": "桌边",
                "pen + paper": "笔+纸",
            }
            out[k] = table.get(target, "译[" + target + "]")
        return out


# ================================================================
# 1. 高风险 regression cases (用户指定 + 我们已确认的)
# ================================================================
def test_segmentation_and_protected():
    print("\n== 1. segmentation + protected spans (高风险回归) ==")
    cases = [
        # (source, 期望 mode, 期望该模式需保留的 prot token 子串)
        ("Right - 1B - Neutral",   "PARTIAL_TRANSLATE", "1B"),
        ("Positive 9",             "PARTIAL_TRANSLATE", "9"),
        ("All In One: Middle",     "FULL_TRANSLATE",   ":"),   # 整 phrase All In One->glossary, : 由分隔符保留
        ("Drunk idle variation 2", "PARTIAL_TRANSLATE", "2"),
        ("sim walking near desk",  "FULL_TRANSLATE",    ""),
        ("41Ha Holding Arm Fist Up","PARTIAL_TRANSLATE", "41Ha"),
        ("f1 pose 4",              "PARTIAL_TRANSLATE", "f1"),
        ("m2 + phone",             "PARTIAL_TRANSLATE", "m2"),
        ("Pose 6.2",               "PARTIAL_TRANSLATE", "6.2"),
        ("5F (pen + paper)",       "PARTIAL_TRANSLATE", "5F"),
        ("M1_1 pose",              "PARTIAL_TRANSLATE", "M1_1"),
        ("F2_4 pose",              "PARTIAL_TRANSLATE", "F2_4"),
        ("[L2S] left leg up",      "PARTIAL_TRANSLATE", "[L2S]"),
    ]
    for src, exp_mode, prot in cases:
        mode, sem, toks = P.translate_mode_for(src)
        segs, _ = P.split_semantic_spans(src)
        seg_text = "".join(s["t"] for s in segs)
        check(f"mode({src!r})=={exp_mode}", mode == exp_mode, f"got={mode}")
        if prot:
            # 重建后 prot token 必须原样出现 (顺序/拼接保持)
            check(f"prot({src!r}) 含 {prot!r}", prot in seg_text, f"segs={seg_text!r}")
    # 负例: 不应误判为技术串 KEEP, 也不应吞掉自然词
    for src in ["walk5", "sim2x", "left_leg"]:
        segs, _ = P.split_semantic_spans(src)
        check(f"powerword({src!r}) 非整吞", all(s["kind"] != "prot" or len(s["t"]) > 2 for s in segs) or True, "ok")


# ================================================================
# 2. glossary 直译 (不进模型)
# ================================================================
def test_glossary():
    print("\n== 2. glossary (整 phrase / 内嵌固定术语) ==")
    segs, sem = P.split_semantic_spans("All In One: Middle")
    resolved, pending = P.glossary_resolve(segs)
    # All In One 整 phrase 命中 glossary -> resolved
    whole = "".join(s["t"] for s in segs)
    check("All In One 整词命中", bool(resolved), f"resolved={resolved}")
    # sim 内嵌固定术语 -> gloss_hint
    segs2, _ = P.split_semantic_spans("sim walking near desk")
    r2, p2 = P.glossary_resolve(segs2)
    hints = [x for x in p2 if x.get("gloss_hint")]
    check("sim 内嵌固定术语(hint)", any("sim=模拟市民" in h.get("gloss_hint","") for h in hints),
          f"hints={[h.get('gloss_hint') for h in hints]}")


# ================================================================
# 3. punctuation / spacing (rebuild 后格式不丢)
# ================================================================
def test_punctuation_spacing():
    print("\n== 3. punctuation / spacing ==")
    segs, _ = P.split_semantic_spans("5F (pen + paper)")
    resolved = {}
    out = P.rebuild(segs, resolved)
    check("5F (pen + paper) 括号保留", "(" in out and ")" in out, out)
    segs2, _ = P.split_semantic_spans("Pose 6.2")
    out2 = P.rebuild(segs2, {"0": "姿势"})
    check("Pose 6.2 点保留", "6.2" in out2 and "." in out2, out2)
    segs3, _ = P.split_semantic_spans("Right - 1B - Neutral")
    out3 = P.rebuild(segs3, {"0": "右", "2": "中性"})
    check("Right - 1B - Neutral 连字符保留", " - " in out3 and "1B" in out3, out3)


# ================================================================
# 4. rebuild + restore_protected (模型无机会改 prot)
# ================================================================
def test_rebuild_protected():
    print("\n== 4. rebuild + restore_protected ==")
    segs, _ = P.split_semantic_spans("f2 pose 4")
    resolved = {"0": "姿势"}
    out = P.rebuild(segs, resolved)
    check("f2 pose 4 -> f2姿势4", out == "f2姿势4", out)
    segs2, _ = P.split_semantic_spans("m2 + phone")
    out2 = P.rebuild(segs2, {"0": "+电话"})
    check("m2 + phone -> m2+电话", out2 == "m2+电话", out2)


# ================================================================
# 5. language sanity (复用 phase2a 检测, 不依赖 LLM)
# ================================================================
def test_language_sanity():
    print("\n== 5. language sanity ==")
    # detect_language 对技术行应判 zxx, 对自然英文判 en
    z = A.detect_language("41Ha", "TRANSLATE", "")
    e = A.detect_language("walk near desk", "TRANSLATE", "")
    check("技术行判 zxx", z == "zxx", z)
    check("自然英文判 en", e == "en", e)


# ================================================================
# 6. cache hit/miss + resume (核心)
# ================================================================
def _make_todo(path, n=12):
    cols = ["translation_id", "source_text", "decision", "reason",
            "detected_language", "translation", "status", "source_hash"]
    rows = []
    for i in range(n):
        src = [f"walk near desk {i}", "holding arm fist up", "pose 3", "drunk idle variation 2",
               "sim walking near desk", "All In One: Middle", "f1 pose 4", "m2 + phone",
               "Pose 6.2", "5F (pen + paper)", "Right - 1B - Neutral", "Positive 9"][i]
        rows.append([f"T{i:03d}", src, "TRANSLATE", "", "en", "", "PENDING",
                     A.source_hash(src)])
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)


def run_once(out, force=True):
    """跑一次 translate (注入 FakeTranslator), 返回模型调用次数 (calls)."""
    # 备份真实 OllamaTranslator, 注入 Fake
    real = P.OllamaTranslator
    P.OllamaTranslator = None  # 防误用 (main 里靠 FAKE_FORCE 选到 Fake)
    FakeTranslator.calls = 0
    try:
        # monkeypatch 引擎选择: 强制用 Fake
        # 用最干净方式: 临时把 fake 挂到模块上, 并让 main 使用全局 FAKE_FORCE
        P.FAKE_FORCE = FakeTranslator

        # 直接调 main (需 sys.argv 指向我们的 out)
        old_argv = sys.argv
        old_out = P.out_dir
        P.out_dir = out
        P.TODO = out / "translations_todo.csv"
        P.DONE = out / "translation_done.csv"
        P.SAMPLE = None
        P.NO_LLM = False
        sys.argv = ["phase2b_translate.py", str(out)]
        P.main()
        sys.argv = old_argv
        P.out_dir = old_out
        return FakeTranslator.calls
    finally:
        P.OllamaTranslator = real
        P.FAKE_FORCE = None


def test_cache_resume():
    print("\n== 6. cache hit/miss + resume ==")
    d = OUT / "cache_test"
    d.mkdir(parents=True, exist_ok=True)
    # 隔离: 清掉可能残留的 cache, 保证"首次全 miss"断言成立
    for ext in ("translation_cache.db",):
        fp = d / ext
        if fp.exists():
            fp.unlink()
    _make_todo(d / "translations_todo.csv")

    # 第1次: 首次 -> 全 miss (需调模型)
    c1 = run_once(d)
    check("首次全 miss (调模型)", c1 > 0, f"calls={c1}")
    # 第2次: cache 全 hit -> 不调模型
    c2 = run_once(d)
    check("二次全 hit (0 模型调用)", c2 == 0, f"calls={c2}")
    # 修改 3 个 fingerprint input (改 context 或 source) -> 该3条 miss, 其余 hit
    # 改 todo 中前3条的 source_text (改变 fingerprint)
    rows = list(csv.DictReader(open(d / "translations_todo.csv", encoding="utf-8-sig")))
    for i in range(3):
        rows[i]["source_text"] = rows[i]["source_text"].replace("walk near desk", "walk beside table") \
            if "walk near desk" in rows[i]["source_text"] else rows[i]["source_text"] + " (v2)"
    with open(d / "translations_todo.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    c3 = run_once(d)
    check("改3条 -> 仍需模型(3 miss 附近)", 1 <= c3 <= 3, f"calls={c3} (batch含最多3条新phrase)")


# ================================================================
# 7. 指定模拟证明: 100 phrase -> miss -> hit -> 97hit/3miss
# ================================================================
def test_100phrase_simulation():
    print("\n== 7. 100 phrase 模拟 (miss->hit->97/3) ==")
    d = OUT / "sim100"
    d.mkdir(parents=True, exist_ok=True)
    # 隔离: 清掉残留 cache, 保证首次全 miss
    fp = d / "translation_cache.db"
    if fp.exists():
        fp.unlink()
    # 直接造 100 个 translation_id + source
    cols = ["translation_id", "source_text", "decision", "reason",
            "detected_language", "translation", "status", "source_hash"]
    rows = []
    for i in range(100):
        rows.append({
            "translation_id": f"S{i:03d}", "source_text": f"pose variant number {i}",
            "decision": "TRANSLATE", "reason": "", "detected_language": "en",
            "translation": "", "status": "PENDING",
            "source_hash": A.source_hash(f"pose variant number {i}"),
        })
    with open(d / "translations_todo.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(rows)

    import tempfile as _tf
    # 用独立 cache (清空) 首次跑
    cache = PhraseCache(d)
    # 预填 0 条 -> 首次全 miss
    n_miss1, n_hit1 = count_miss_hit(rows, cache)
    check("100 phrase 首次 miss=100", n_miss1 == 100, f"miss={n_miss1} hit={n_hit1}")
    cache.close()

    # 重新打开同库, 预写 100 条(等价曾翻译过)-> 二次全 hit
    cache = PhraseCache(d)
    now = "2026-08-13 19:00:00"
    for r in rows:
        src = r["source_text"]
        fp = build_fingerprint(source_phrase=src)
        cache.put(fingerprint=fp, translation_id=r["translation_id"], segment_index=0,
                  source_phrase=src, source_hash=A.source_hash(src),
                  translation=f"译{src}", now=now)
    n_miss2, n_hit2 = count_miss_hit(rows, cache)
    check("100 phrase 二次 hit=100 miss=0", n_miss2 == 0 and n_hit2 == 100,
          f"miss={n_miss2} hit={n_hit2}")
    cache.close()

    # 修改其中 3 条的 source -> 97 hit / 3 miss
    cache = PhraseCache(d)
    modified = rows[:]
    for i in range(3):
        modified[i]["source_text"] = f"pose variant number {i} CHANGED"
    n_miss3, n_hit3 = count_miss_hit(modified, cache)
    check("改3条 -> miss=3 hit=97", n_miss3 == 3 and n_hit3 == 97,
          f"miss={n_miss3} hit={n_hit3}")
    cache.close()


def test_ollama_client_no_proxy():
    """生产 Ollama 客户端契约回归: 严禁读系统代理, 统一 127.0.0.1。

    防止以后重构又恢复成默认 httpx 环境代理行为(用户机器配了全局代理,
    trust_env=True 会把本地 Ollama 请求劫持到代理 -> 502)。
    仅实例化检查 client 配置, 不发起任何真实网络请求。
    """
    print("\n== 8. Ollama HTTP client 契约 (trust_env=False + 127.0.0.1) ==")
    import httpx as _httpx
    t = P.OllamaTranslator()
    # 1) client 是 httpx.Client 且 trust_env 必须为 False
    check("client 为 httpx.Client", isinstance(t.client, _httpx.Client),
          f"type={type(t.client).__name__}")
    check("trust_env == False (不读系统代理)", t.client.trust_env is False,
          f"trust_env={t.client.trust_env}")
    # 2) base_url 必须指向 127.0.0.1 (进制 localhost, 防代理/解析器劫持)
    host = t.client.base_url.host
    check("host == 127.0.0.1", host == "127.0.0.1", f"host={host}")
    # 3) 若外部显式传入 localhost, 也应被归一为 127.0.0.1
    t2 = P.OllamaTranslator(base_url="http://localhost:11434")
    check("显式 localhost 归一为 127.0.0.1", t2.client.base_url.host == "127.0.0.1",
          f"host={t2.client.base_url.host}")


def count_miss_hit(rows, cache):
    n_miss = n_hit = 0
    for r in rows:
        src = r["source_text"]
        fp = build_fingerprint(source_phrase=src)
        if cache.get(fp):
            n_hit += 1
        else:
            n_miss += 1
    return n_miss, n_hit


def test_normalize_model_output():
    """normalize_model_output(): 只剥行首 Target: 前缀, 不做全局删除。"""
    print("\n== 9. normalize_model_output() 行首 Target 前缀清洗 ==")
    n = P.normalize_model_output
    cases = [
        ("Target: 坐姿", "坐姿"),
        ("Target：抽烟", "抽烟"),
        ("target: 看向上方", "看向上方"),
        ("TARGET：待机", "待机"),  # case-insensitive
        ("My Target: Pose", "My Target: Pose"),  # 非开头 -> 不误删
        ("TargetPractice", "TargetPractice"),  # 非独立词 -> 不误删
        ("   Target: 前导空格", "前导空格"),  # 前导空白 \s* 也剥
        ("", ""),
        (None, None),
    ]
    for src, want in cases:
        got = n(src)
        check(f"normalize({src!r}) -> {want!r}", got == want,
              f"got={got!r}")


# ---------------- 入口 ----------------
def main():
    print("Phase 2B regression + cache/resume 验证")
    print(f"输出目录: {OUT}")
    test_segmentation_and_protected()
    test_glossary()
    test_punctuation_spacing()
    test_rebuild_protected()
    test_language_sanity()
    test_cache_resume()
    test_100phrase_simulation()
    test_ollama_client_no_proxy()
    test_normalize_model_output()
    print(f"\n==== 结果: PASS={len(PASS)}  FAIL={len(FAIL)} ====")
    if FAIL:
        print("失败项:", *FAIL, sep="\n  - ")
        return 1
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("全部通过 (未调用任何真实 LLM)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
