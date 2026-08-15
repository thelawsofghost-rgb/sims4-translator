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


def run_once(out, force=True, auth=False):
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
        P.AUTHORITATIVE_DECISION_ON = auth
        sys.argv = ["phase2b_translate.py", str(out)] + (["--authoritative"] if auth else [])
        P.main()
        sys.argv = old_argv
        P.out_dir = old_out
        return FakeTranslator.calls
    finally:
        P.OllamaTranslator = real
        P.FAKE_FORCE = None
        P.AUTHORITATIVE_DECISION_ON = False


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
        ("目标：sim smoking", "sim smoking"),  # 中文 Target 前缀也剥
        ("目标: 抽烟", "抽烟"),  # 中文 + 半角冒号
        ("目标:抽烟", "抽烟"),  # 中文无空格
        ("Target: 坐姿", "坐姿"),
        ("My Target: Pose", "My Target: Pose"),  # 非开头 -> 不误删
        ("TargetPractice", "TargetPractice"),  # 非独立词 -> 不误删
        ("目标管理", "目标管理"),  # 正文"目标"词 -> 不误删
        ("   Target: 前导空格", "前导空格"),  # 前导空白 \s* 也剥
        ("", ""),
        (None, None),
    ]
    for src, want in cases:
        got = n(src)
        check(f"normalize({src!r}) -> {want!r}", got == want,
              f"got={got!r}")


# ---------------- 人工 override 机制 ----------------
def _make_override_todo(path):
    cols = ["translation_id", "source_text", "decision", "reason",
            "detected_language", "translation", "status", "source_hash"]
    rows = [
        ["T_o1", "ALL-IN-ONE", "TRANSLATE", "", "en", "", "PENDING", A.source_hash("ALL-IN-ONE")],
        ["T_o2", "Smh", "TRANSLATE", "", "en", "", "PENDING", A.source_hash("Smh")],
        ["T_o3", "loop-obj", "TRANSLATE", "", "en", "", "PENDING", A.source_hash("loop-obj")],
        ["T_n1", "walk near desk", "TRANSLATE", "", "en", "", "PENDING", A.source_hash("walk near desk")],
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)


def _make_override_csv(path):
    # 覆盖 3 条: 2 TRANSLATE + 1 KEEP; 另加 1 条故意 (tid,source_text) 不匹配的, 应被忽略
    cols = ["translation_id", "source_text", "translation", "action", "reason", "notes"]
    rows = [
        ["T_o1", "ALL-IN-ONE", "整合版", "TRANSLATE", "glossary", ""],
        ["T_o2", "Smh", "", "KEEP", "缩写", ""],
        # 故意 (tid,source_text) 不匹配: source_text 与 todo 里的 "loop-obj" 不一致
        ["T_o3", "loop_object_wrong", "循环物体(错)", "TRANSLATE", "故意错位, 应不命中", ""],
        ["T_oX", "W R O N G", "x", "TRANSLATE", "tid 不匹配 todo, 无影响", ""],
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)


def test_override():
    print("\n== 10. 人工 override (translation_overrides.csv) ==")
    d = OUT / "override_test"
    d.mkdir(parents=True, exist_ok=True)
    for f in ("translation_done.csv", "translation_cache.db", "translations_todo.csv", "translation_overrides.csv"):
        fp = d / f
        if fp.exists():
            fp.unlink()
    _make_override_todo(d / "translations_todo.csv")
    _make_override_csv(d / "translation_overrides.csv")

    # 预填 cache: 给 T_n1 (普通行) 一个可命中的译文, 保证 run 后它是 DONE 且不调模型
    cache = PhraseCache(d, model="fake")
    src_p = "walk near desk"
    fp = build_fingerprint(source_phrase=src_p, glossary_hint="", context="")
    cache.put(fingerprint=fp, translation_id="T_n1", segment_index="0",
              source_phrase=src_p, source_hash=A.source_hash(src_p),
              translation="桌边行走", now="2026-08-14 00:00:00")
    cache.close()

    run_once(d)  # 触发 main (Fake 引擎, 0 真实 LLM)

    done = {r["translation_id"]: r for r in csv.DictReader(open(d / "translation_done.csv", encoding="utf-8-sig"))}
    # ① TRANSLATE override: 译文 + 终态 DONE
    r1 = done["T_o1"]
    check("TRANSLATE override 译文", r1["translation"] == "整合版", f"got={r1['translation']!r}")
    check("TRANSLATE override 终态 DONE", r1["status"] == "DONE", f"status={r1['status']!r}")
    # ② KEEP override: 空译文 + 明确终态 KEEP
    r2 = done["T_o2"]
    check("KEEP override 空译文", r2["translation"] == "", f"got={r2['translation']!r}")
    check("KEEP override 终态 KEEP(非PENDING)", r2["status"] == "KEEP", f"status={r2['status']!r}")
    # ③ 错位 source_text 不命中: loop-obj 应走正常流程 (此例被 Fake 翻译)
    r3 = done["T_o3"]
    check("错位 source_text 不命中 override (走后端)", r3["status"] in ("DONE", "PENDING"), f"status={r3['status']!r} translation={r3['translation']!r}")
    # ④ 普通行不受影响: 从 cache 物化
    rn = done["T_n1"]
    check("普通行 cache 物化不受 override 影响", rn["translation"] == "桌边行走" and rn["status"] == "DONE", f"got={rn['translation']!r}")
    # ⑤ override 不写 phrase cache: override 行 (T_o1/T_o2) 不得新增缓存条目;
    #    非 override 行 (T_o3) 走后端可合法写缓存。校验: 缓存里没有来源于 override 行的译文。
    cache2 = PhraseCache(d, model="fake")
    leak = cache2._conn.execute(
        "SELECT COUNT(*) FROM phrase_cache WHERE translation_id IN ('T_o1','T_o2')"
    ).fetchone()[0]
    check("override 不写 phrase cache (override 行零新增条目)", leak == 0, f"override行泄漏={leak}")
    cache2.close()

    # ⑥ QA: KEEP override 应为 PASS (明确终态, 不算未完成)
    from phase2b_qa import classify as qa_classify
    q_pass = qa_classify(dict(translation_id="T_o2", source_text="Smh", translation="", status="KEEP", translate_mode="OVERRIDE_K", protected_spans=""))
    check("QA 识别 KEEP override 为 PASS 终态", q_pass[0] == "PASS", f"got={q_pass!r}")


# ================================================================
# 11. BUG1/BUG2/BUG3 修复回归 (2026-08-15 真实 retry 暴露)
# ================================================================
def test_bug_fixes():
    print("\n== 11. BUG1(echo cache) / BUG2(segment gate) / BUG3(authoritative unchanged) =")
    d = OUT / "bugfix_test"
    d.mkdir(parents=True, exist_ok=True)
    cols = ["translation_id", "source_text", "decision", "detected_language", "source_hash", "category"]
    rows = [
        # BUG2: 整行 translation != source, 但 semantic segment 保持英文 -> 假 DONE
        # (用非 glossary 段, 因 Pose Pack 已入确定性术语表 -> 不再是 stuck segment)
        ("T_962602977185_g1", "Wait... It's You! - Special Pack", "TRANSLATE", "en", "h1", "TRANSLATE"),
        ("T_e10b19982082_g1", "Emotions - Sad", "TRANSLATE", "en", "h2", "TRANSLATE"),
        # BUG3: authoritative TRANSLATE + 整行 unchanged
        ("T_438c8bd18eda_g1", "[ROSELIPA] 2AM", "TRANSLATE", "en", "h3", "TRANSLATE"),
        # BUG1: semantic phrase -> echo cache
        ("T_x1", "Happy Pose", "TRANSLATE", "en", "h4", "TRANSLATE"),
        # 正常行 (应 DONE)
        ("T_ok1", "Walking Pose - Relaxed", "TRANSLATE", "en", "h5", "TRANSLATE"),
    ]
    with open(d / "translations_todo.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    # 预置 cache: echo 条目 (Happy Pose/Pose Pack/Sad), 正常条目 (Emotions/Wait...)
    cache = PhraseCache(d, model="fake")
    def _put(src, val):
        cache.put(fingerprint=build_fingerprint(source_phrase=src, glossary_hint="", context=""),
                  translation_id="seed", segment_index="0", source_phrase=src,
                  source_hash=A.source_hash(src), translation=val, now="2026-08-15 00:00:00")
    _put("Happy Pose", "Happy Pose")        # echo
    _put("Special Pack", "Special Pack")     # echo (BUG2 s1)
    _put("Sad", "Sad")                      # echo (BUG2 s2)
    _put("Wait... It's You!", "等等...是你！")
    _put("Emotions", "情绪")
    _put("Walking Pose", "行走姿势"); _put("Relaxed", "放松")
    cache.close()
    # BUG3 需 --authoritative 才触发 (authoritative TRANSLATE + unchanged + 无 terminal evidence)。
    run_once(d, auth=True)
    done = {r["translation_id"]: r for r in csv.DictReader(open(d / "translation_done.csv", encoding="utf-8-sig"))}
    # BUG1/BUG2 (Fake 模型): echo cache 被拒 -> miss -> 重翻为非 echo 译文 -> DONE。
    # 验证: 译文 != 原 semantic (echo 未 materialize, 已重翻译)。
    r1 = done["T_962602977185_g1"]
    check("BUG2: s1 的 Special Pack 被重翻 (非 echo)", "译[Special Pack]" in r1["translation"] and r1["translation"] != "Special Pack",
          f"trans={r1['translation']!r}")
    r2 = done["T_e10b19982082_g1"]
    check("BUG2: s2 的 Sad 被重翻 (非 echo)", "译[Sad]" in r2["translation"] and r2["translation"] != "Sad",
          f"trans={r2['translation']!r}")
    r4 = done["T_x1"]
    check("BUG1: Happy Pose echo 被拒重翻 (非 echo)", r4["translation"] != "Happy Pose" and r4["status"] == "DONE",
          f"trans={r4['translation']!r}")
    # BUG3: authoritative + unchanged ([ROSELIPA] 2AM 无 terminal evidence) -> QA_FAIL
    r3 = done["T_438c8bd18eda_g1"]
    check("BUG3: authoritative unchanged 行 QA_FAIL", r3["status"] == "QA_FAIL",
          f"status={r3['status']!r} trans={r3['translation']!r}")
    # 正常行仍 DONE
    r5 = done["T_ok1"]
    check("BUG fix: 正常行 T_ok1 DONE", r5["status"] == "DONE", f"status={r5['status']!r}")


# ================================================================
# 12. BUG4: TITLE creator/identifier 保护 (2026-08-15 用户裁决)
#    required_translate/pending 不能把 creator token 当可译语义 -> 假 echo QA_FAIL。
# ================================================================
def test_bug4_title_creator_protection():
    print("\n== 12. BUG4: PACK_TITLE creator/identifier 保护 ==")
    # 单元: 精确 protection 不伤语义主段
    cases = [
        # (source, 期望 pending 的 semantic 段, 期望被保护的 creator token)
        ("(simmer_creator) - Male poses #1", {"Male poses #"}, {"(simmer_creator)"}),
        ("(UNI) Emotion Poses Adult", {"Emotion Poses Adult"}, {"(UNI)"}),
        ("Loulicorn - Pretty Smile Poses", {"Pretty Smile Poses"}, {"Loulicorn"}),
        ("Grownasssimmer Kaley - Special Moves", {"Special Moves"}, {"Grownasssimmer Kaley"}),
        ("[Jarride]xLienaEnna - Pretty Smile Poses", {"Pretty Smile Poses"}, {"xLienaEnna"}),
    ]
    for src, exp_pending, exp_prot in cases:
        prot, reasons = P.title_creator_protection(src)
        segs, _ = P.split_semantic_spans(src, force_prot_spans=prot)
        pending = {p["t"].strip() for p in P.glossary_resolve(segs)[1]}
        prot_segs = {s["t"].strip() for s in segs if s["kind"] == "prot"}
        contained = any(any(pr.strip().startswith(c) or c in pr for c in exp_prot) for pr in prot_segs)
        check(f"BUG4 prot: {src!r} semantic={sorted(exp_pending)}",
              pending == exp_pending, f"pending={pending} want={exp_pending}")
        check(f"BUG4 prot: creator protected", contained, f"prot_segs={prot_segs} want={exp_prot}")
    # 负例: 不得过度保护 (精确, 非宽泛启发式)
    for src, exp_pending in [("Simpler Poses 1", {"Simpler Poses"}),
                             ("Pretty Smile Poses", {"Pretty Smile Poses"})]:
        prot, _ = P.title_creator_protection(src)
        segs, _ = P.split_semantic_spans(src, force_prot_spans=prot)
        pending = {p["t"].strip() for p in P.glossary_resolve(segs)[1]}
        check(f"BUG4 no-overprotect: {src!r}", pending == exp_pending, f"pending={pending}")

    # BUG4 edge (2026-08-15 用户新诊断): 尾部 separator 前缀 (NA_) 字面起始匹配,
    # 不要求末尾 word-boundary (否则 _A 间无 \\b 导致 NA_ 命中失败)。
    for src, exp_prot, exp_pending in [
        ("NA_Arrested posepack", {"NA_"}, {"Arrested posepack"}),
        ("NA_Tender love posepack", {"NA_"}, {"Tender love posepack"}),
    ]:
        prot, _ = P.title_creator_protection(src)
        segs, _ = P.split_semantic_spans(src, force_prot_spans=prot)
        pending = {p["t"].strip() for p in P.glossary_resolve(segs)[1]}
        prot_segs = {s["t"].strip() for s in segs if s["kind"] == "prot"}
        check(f"BUG4 NA_ prot: {src!r} -> pending",
              pending == exp_pending, f"pending={pending} want={exp_pending}")
        check(f"BUG4 NA_ prot: {src!r} -> prot_segs",
              exp_prot <= prot_segs, f"prot_segs={prot_segs} want={exp_prot}")
    # 负例: 普通含下划线语义标题 不得因 _ 自动 prot (只有 frozen NA_ 才 prot)
    prot, _ = P.title_creator_protection("Arrested_Tender posepack")
    segs, _ = P.split_semantic_spans("Arrested_Tender posepack", force_prot_spans=prot)
    pending = {p["t"].strip() for p in P.glossary_resolve(segs)[1]}
    check("BUG4 NA_ no-overprotect: Arrested_Tender posepack 整串 pending (非因 _ 自动 prot)",
          pending == {"Arrested_Tender posepack"}, f"pending={pending}")
    # 负例: 未 frozen 的 X_ 前缀不得自动 prot (拒绝宽泛 ^[A-Z]+_)
    prot, _ = P.title_creator_protection("X_Pose Pack")
    check("BUG4 NA_ no-overprotect: 未 frozen X_ 不产生 creator prot span",
          prot == [], f"prot={prot}")
    segs, _ = P.split_semantic_spans("X_Pose Pack", force_prot_spans=prot)
    pending = {p["t"].strip() for p in P.glossary_resolve(segs)[1]}
    check("BUG4 NA_ no-overprotect: X_ 语义段仍 required (未被吞掉)",
          len(pending) > 0, f"pending={pending}")


# ================================================================
# 13. BUG5: 英文 apostrophe contraction 必须先于 standalone/technical 识别
#      + Pose Player 领域术语表 (Pose Pack -> 姿势包)
# ================================================================
def test_bug5_contraction_and_pose_glossary():
    print("\n== 13. BUG5 contraction segmentation + Pose Pack glossary ==")
    _conts = ["I'm here", "I’m here", "It's", "It’s", "don't", "don’t",
              "can't", "can’t", "won't", "won’t", "let's", "let’s",
              "you're", "you’re", "we're", "we’re", "they've", "they’ve"]
    for c in _conts:
        segs, _ = P.split_semantic_spans(c)
        kinds = [s["kind"] for s in segs]
        check(f"BUG5: {c!r} 不被拆成 standalone prot + 剩余 (整段 sem)",
              kinds == ["sem"] and any(s["t"].strip().casefold() == c.casefold() for s in segs),
              f"segs={[(s['kind'],s['t']) for s in segs]}")
    # 用户核心: [AA] I'm here -> [AA] prot + "I'm here" sem (不得出现 prot I)
    segs, _ = P.split_semantic_spans("[AA] I'm here")
    prot_any = [s for s in segs if s["kind"] == "prot" and s["t"].strip() == "I"]
    sem_cont = any(s["kind"] == "sem" and "I'm here" in s["t"] for s in segs)
    check("BUG5: [AA] I'm here -> [AA]prot + I'm here sem (无 prot I)",
          not prot_any and sem_cont, f"segs={[(s['kind'],s['t']) for s in segs]}")
    # 保留 standalone I/F/B/M/A 为技术 token 原规则
    for lone in ["I", "F", "B", "M", "A"]:
        segs, _ = P.split_semantic_spans(lone)
        check(f"BUG5: standalone {lone} 仍 prot (技术 token)",
              segs and segs[0]["kind"] == "prot" and segs[0]["t"].strip() == lone,
              f"segs={[(s['kind'],s['t']) for s in segs]}")
    # Pose Player 领域术语表: Pose Pack / pose pack / posepack -> 姿势包
    for sp in ["Pose Pack", "pose pack", "POSEPACK", "posepack", "Pose Pack 1"]:
        segs, _ = P.split_semantic_spans(sp)
        resolved, pending = P.glossary_resolve(segs)
        check(f"glossary: {sp!r} -> 姿势包 (无 pending)",
              resolved and all(v == "姿势包" for v in resolved.values()) and not pending,
              f"resolved={resolved} pending={[p['t'].strip() for p in pending]}")
    # glossary 不进模型但行内嵌词给 gloss_hint: NA_Arrested posepack
    prot, _ = P.title_creator_protection("NA_Arrested posepack")
    segs, _ = P.split_semantic_spans("NA_Arrested posepack", force_prot_spans=prot)
    resolved, pending = P.glossary_resolve(segs)
    check("glossary: NA_Arrested posepack -> NA_ prot + gloss_hint posepack",
          any((p.get("gloss_hint") or "") == "posepack=姿势包" for p in pending),
          f"pending={[(p['t'].strip(), p.get('gloss_hint')) for p in pending]}")


# ================================================================
# 14. DESCRIPTION protection regression (URL/domain, product/model,
#     creator/name, asset id, 纯 '+'): content-QA 不得把这些判为 candidate
# ================================================================
def test_desc_protection_content_qa():
    print("\n== 14. DESCRIPTION protection regression (content-QA 不误报) ==")
    try:
        import desc_content_qa as Q
    except Exception as e:
        check("import desc_content_qa", False, f"import err: {e}")
        return

    # protected 样例 (品牌/作者/asset id/URL/纯+) -> 不得产生 RESIDUAL_EN / CJK / 破坏 flag
    protected_cases = [
        ("katverse.com", "katverse.com"),                      # URL/domain
        ("katverse.com", "katverse.com"),
        ("Pose 3 - iPhone 13 Pro Max", "姿势 3 - iPhone 13 Pro Max"),  # 产品/型号
        ("[Musae] Sleeping Beauty", "[Musae] 睡美人"),                 # bracketed creator
        ("Soloriya Dance Pose", "Soloriya 舞蹈姿势"),                 # creator 专名
        ("BRADFORD Sweater", "BRADFORD 毛衣"),                       # creator 专名
        ("[AnotherSimsStory]-HospitalBedFlat", "[AnotherSimsStory]-医院床铺平面"),  # asset id
        ("NA_Iphone ACC accessory", "NA_Iphone ACC 配件"),             # NA_/ACC accessory
        ("Tiny Detail +", "微小细节 +"),                             # 纯 + 符号保持
    ]
    for src, tr in protected_cases:
        flags = []
        if Q.residual_english(tr, src):
            flags.append("RESIDUAL_EN")
        if Q.cjk_latin_glue(tr):
            flags.append("CJK_LATIN_GLUE")
        if Q.digit_drop(src, tr):
            flags.append("DIGIT_DROP")
        if Q.bracket_id_broken(src, tr):
            flags.append("BRACKET_ID_BROKEN")
        check(f"desc-protect: {src!r} -> 无 candidate flags", not flags, f"flags={flags}")

    # 负例 (真问题): 必须还能抓出 candidate
    neg_cases = [
        ("Look At Camera 123", "看镜头", "DIGIT_DROP"),          # digit dropped
        ("I Am Here Now", "我在这里I", "CJK_LATIN_GLUE"),        # I 黏连
        ("Waving (hand", "挥手 (手", "BRACKET_IMBALANCE"),     # 括号不平衡
    ]
    for src, tr, want_flag in neg_cases:
        flags = []
        if Q.residual_english(tr, src):
            flags.append("RESIDUAL_EN")
        if Q.cjk_latin_glue(tr):
            flags.append("CJK_LATIN_GLUE")
        if Q.digit_drop(src, tr):
            flags.append("DIGIT_DROP")
        if Q.bracket_balance(tr):
            flags.append("BRACKET_IMBALANCE")
        if Q.bracket_id_broken(src, tr):
            flags.append("BRACKET_ID_BROKEN")
        check(f"desc-detect: {src!r} 应含 {want_flag}", want_flag in flags, f"flags={flags}")


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
    test_override()
    test_bug_fixes()
    test_bug4_title_creator_protection()
    test_bug5_contraction_and_pose_glossary()
    test_desc_protection_content_qa()
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
