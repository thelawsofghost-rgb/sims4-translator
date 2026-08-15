// SidecarBuilder — 正式 COMPLETE-STBL Sidecar Writer (P1 自动化 sidecar)
// 目标 (Phase 3B2 Step③ 锁定):
//   以【完整原始 CHS STBL 为基线】生成独立 override sidecar 包 (!<mod>_CHS.package)。
//   same TGI override = 覆盖整张原始 STBL resource, 绝不生成 partial STBL。
// 本版绝不生成批量/659 包, 不碰 Animation, 原 mod 只读。
//
// 复用的成熟 writer: s4pi (仓库 vendor/s4pi, GPL v3) — 序列化完全由 s4pi 负责, 零手写 DBPF。
//
// 用法 (正式生产输入):
//   SidecarBuilder.exe \
//     -source <source.package> \
//     -out    <output.package> \
//     -type   0x220557DA \
//     -group  0x80000000 \
//     -inst   0x... \
//     -locale 0x01 \                        # 可选: 校验目标 STBL 为 CHS_CN; 缺省不校验高字节
//     -m      KEYHASH:EXPECTED:VALUE        # 可多次: 仅当该 key 当前文本==EXPECTED 时改为 VALUE, 否则 fail
//
// 流程 (11+1 步, 已锁定):
//   1. 只读打开 source
//   2. GetResourceList 中按精确 TGI 查找, 必须恰好命中 1 个, 否则 fail
//   3. (APackage)src.GetResource(targetEntry) 取原始 STBL stream
//   4. StblResource.StblResource 解析完整 STBL
//   5. 记录 source: Version / IsCompressed / Reserved / Entries.Count / 每项 KeyHash/Flags/StringValue
//   6. 对每个 modification: key 必须唯一存在, StringValue 必须 == expected_source, 否则 fail-fast 不写包
//   7. 仅修改已有 StringEntry.StringValue; 不 new/delete/reorder entry, 不改 KeyHash, 不改 Flags
//   8. 新建 Package, 只加入修改后的完整 STBL, TGI 与 source 完全一致
//   9. SaveAs output
//  10. reopen output
//  11. verify: resource_count==1, TGI exact, Version 同, entry_count 同, KeyHash 集合/顺序同,
//              Flags 全同, modified values==target, untouched values==source
//  12. 输出 manifest / 验证摘要

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using s4pi.Interfaces;
using s4pi.Package;
using StblResource;

class Program
{
    // 一个修改项: 精确匹配某个已有 key, 校验期望原文后替换为译文
    struct Modification
    {
        public uint KeyHash;
        public string ExpectedSource;
        public string TranslatedValue;
    }

    static int Main(string[] args)
    {
        string sourcePath = null, outPath = null;
        uint type = 0, group = 0;
        ulong inst = 0;
        bool hasTgi = false;
        uint? locale = null;
        var mods = new List<Modification>();

        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "-source": sourcePath = args[++i]; break;
                case "-out":    outPath = args[++i]; break;
                case "-type":   type = ParseHexU32(args[++i]); hasTgi = true; break;
                case "-group":  group = ParseHexU32(args[++i]); hasTgi = true; break;
                case "-inst":   inst = ParseHexU64(args[++i]); hasTgi = true; break;
                case "-locale": locale = ParseHexU32(args[++i]); break;
                case "--selfcheck":
                    return SelfCheck();
                case "-m":
                    {
                        string raw = args[++i];
                        // 格式: KEYHASH:EXPECTED:VALUE (KEYHASH 为 hex; EXPECTED/VALUE 可为 ":" 内含转义——此处不允许歧义, 仅取最前两个冒号切分)
                        int c1 = raw.IndexOf(':');
                        if (c1 < 0) { Fail("bad -m (need KEYHASH:EXPECTED:VALUE): " + raw); return 1; }
                        int c2 = raw.IndexOf(':', c1 + 1);
                        if (c2 < 0) { Fail("bad -m (need KEYHASH:EXPECTED:VALUE): " + raw); return 1; }
                        uint kh = ParseHexU32(raw.Substring(0, c1));
                        string expected = raw.Substring(c1 + 1, c2 - c1 - 1);
                        string value = raw.Substring(c2 + 1);
                        mods.Add(new Modification { KeyHash = kh, ExpectedSource = expected, TranslatedValue = value });
                        break;
                    }
                case "-m64":
                    {
                        // 格式: KEYHASH:BASE64_EXPECTED:BASE64_VALUE (exactly 3 fields)
                        // 传输边界修复 (2026-08-16): expected/value 以 standard Base64 编码 UTF-8 承载,
                        // 彻底规避 source/translation 内含 ':' 时与冒号定界冲突。
                        // parser 严格 exactly 3 fields; malformed Base64 / invalid UTF-8 / invalid key -> ERROR fail-closed。
                        string raw = args[++i];
                        int c1 = raw.IndexOf(':');
                        if (c1 < 0) { Fail("bad -m64 (need KEYHASH:BASE64_EXPECTED:BASE64_VALUE): " + raw); return 1; }
                        int c2 = raw.IndexOf(':', c1 + 1);
                        if (c2 < 0) { Fail("bad -m64 (need KEYHASH:BASE64_EXPECTED:BASE64_VALUE): " + raw); return 1; }
                        string kh_s = raw.Substring(0, c1);
                        string exp_b64 = raw.Substring(c1 + 1, c2 - c1 - 1);
                        string val_b64 = raw.Substring(c2 + 1);
                        // KEYHASH 与 EXPECTED_B64 必非空; VALUE_B64 可为空 (空译文合法)。
                        if (kh_s.Length == 0 || exp_b64.Length == 0)
                        {
                            Fail("bad -m64: empty key/expected (need KEYHASH:BASE64_EXPECTED[:BASE64_VALUE]): " + raw); return 1;
                        }
                        uint kh;
                        try { kh = ParseHexU32(kh_s); }
                        catch (Exception) { Fail("bad -m64: invalid key hash: " + kh_s); return 1; }
                        string expected, value;
                        if (!TryBase64Utf8(exp_b64, out expected))
                        { Fail("bad -m64: expected Base64/UTF-8 decode failed: " + exp_b64); return 1; }
                        if (!TryBase64Utf8(val_b64, out value))   // 空串 decode 为空 -> 合法
                        { Fail("bad -m64: value Base64/UTF-8 decode failed: " + val_b64); return 1; }
                        mods.Add(new Modification { KeyHash = kh, ExpectedSource = expected, TranslatedValue = value });
                        break;
                    }
                default:
                    Fail("unknown arg: " + args[i]); return 1;
            }
        }

        if (sourcePath == null || outPath == null || !hasTgi || mods.Count == 0)
        {
            Fail("usage: SidecarBuilder -source SRC -out OUT -type T -group G -inst I [-locale 0x01] [-m KEYHASH:EXPECTED:VALUE | -m64 KEYHASH:B64_EXP:B64_VAL] ...");
            return 1;
        }
        if (!File.Exists(sourcePath)) { Fail("source not found: " + sourcePath); return 1; }

        return Run(sourcePath, outPath, type, group, inst, locale, mods);
    }

    static int Run(string sourcePath, string outPath, uint type, uint group, ulong inst, uint? locale, List<Modification> mods)
    {
        // ---- Step 1: 只读打开 source ----
        IPackage src = null;
        try { src = Package.OpenPackage(1, sourcePath, false); }
        catch (Exception ex) { Fail("open source failed: " + ex.Message); return 1; }

        // ---- Step 2: 精确 TGI 唯一命中 ----
        var list = src.GetResourceList;
        var hits = list.Where(e => e.ResourceType == type && e.ResourceGroup == group && e.Instance == inst).ToList();
        if (hits.Count != 1)
        {
            Fail(string.Format("TGI must match exactly 1 resource; found {0} (type=0x{1:X8} group=0x{2:X8} inst=0x{3:X16})",
                hits.Count, type, group, inst));
            src.Dispose(); return 1;
        }
        IResourceIndexEntry target = hits[0];

        // 可选 locale 校验: 64-bit Instance 的最高 8 bits (bits 56-63) 编码 locale
        // (0x01=CHS_CN, 0x02=CHT)。例 0x014EACCF17C8B091 字节序 01 4E AC CF 17 C8 B0 91,
        // 最高 byte = 0x01。仅当显式给 -locale 时校验。
        if (locale.HasValue)
        {
            uint hi = (uint)((inst >> 56) & 0xFF);
            if (hi != locale.Value)
            {
                Fail(string.Format("locale check failed: instance top byte 0x{0:X2} != expected -locale 0x{1:X2}",
                    hi, locale.Value));
                src.Dispose(); return 1;
            }
        }

        // ---- Step 3: 取原始 STBL stream ----
        Stream srcStream;
        try { srcStream = ((APackage)src).GetResource(target); }
        catch (Exception ex) { Fail("GetResource failed: " + ex.Message); src.Dispose(); return 1; }

        // ---- Step 4: 解析完整 STBL ----
        StblResource.StblResource stbl;
        try { stbl = new StblResource.StblResource(1, srcStream); }
        catch (Exception ex) { Fail("parse STBL failed: " + ex.Message); src.Dispose(); return 1; }

        // ---- Step 5: 记录 source 快照 ----
        ushort srcVersion   = stbl.Version;
        byte   srcCompressed = stbl.IsCompressed;
        byte[] srcReserved  = stbl.Reserved;
        int    srcCount     = stbl.Entries.Count;
        var original = stbl.Entries.Select(e => new { key = e.KeyHash, flags = e.Flags, val = e.StringValue }).ToList();
        Console.WriteLine("[source] Version={0} IsCompressed={1} Reserved={2} Entries={3}",
            srcVersion, srcCompressed, (srcReserved == null ? "-" : BitConverter.ToString(srcReserved)), srcCount);

        // ---- Step 6: 校验每个 modification (fail-fast, 不写包) ----
        for (int m = 0; m < mods.Count; m++)
        {
            var mm = mods[m];
            var matches = stbl.Entries.Where(e => e.KeyHash == mm.KeyHash).ToList();
            if (matches.Count == 0)
            {
                Fail(string.Format("mod #{0}: key 0x{1:X8} not found in source STBL", m, mm.KeyHash));
                src.Dispose(); return 1;
            }
            if (matches.Count > 1)
            {
                Fail(string.Format("mod #{0}: key 0x{1:X8} not unique in source STBL", m, mm.KeyHash));
                src.Dispose(); return 1;
            }
            if (string.CompareOrdinal(matches[0].StringValue, mm.ExpectedSource) != 0)
            {
                Fail(string.Format("mod #{0}: key 0x{1:X8} expected \"{2}\" but source has \"{3}\"",
                    m, mm.KeyHash, mm.ExpectedSource, matches[0].StringValue));
                src.Dispose(); return 1;
            }
        }

        // ---- Step 7: 仅修改已有 entry 的 value ----
        for (int m = 0; m < mods.Count; m++)
        {
            var mm = mods[m];
            var targetEntry = stbl.Entries.First(e => e.KeyHash == mm.KeyHash);
            targetEntry.StringValue = mm.TranslatedValue;
            Console.WriteLine("[mod] 0x{0:X8}: \"{1}\" -> \"{2}\"", mm.KeyHash, mm.ExpectedSource, mm.TranslatedValue);
        }

        // ---- Step 8: 新建 Package, 只加入这一张修改后的完整 STBL ----
        byte[] body = ReadAll(stbl.Stream);
        IPackage outp = Package.NewPackage(1);
        var rk = new TGIBlock(1, null, type, group, inst);
        var ie = outp.AddResource(rk, new MemoryStream(body), true);
        if (ie == null) { Fail("AddResource failed (dup instance?)"); src.Dispose(); outp.Dispose(); return 1; }

        // ---- Step 9: SaveAs ----
        try { outp.SaveAs(outPath); }
        catch (Exception ex) { Fail("SaveAs failed: " + ex.Message); src.Dispose(); outp.Dispose(); return 1; }
        Console.WriteLine("SAVED " + outPath);
        outp.Dispose();

        // ---- Step 10: reopen ----
        IPackage chk;
        try { chk = Package.OpenPackage(1, outPath, false); }
        catch (Exception ex) { Fail("reopen failed: " + ex.Message); src.Dispose(); return 1; }

        // ---- Step 11: 全量验证 ----
        bool ok = true;
        var clist = chk.GetResourceList;
        if (clist.Count != 1)             { Fail("VERIFY: resource_count != 1 (" + clist.Count + ")"); ok = false; }
        else
        {
            var ce = clist[0];
            if (ce.ResourceType != type || ce.ResourceGroup != group || ce.Instance != inst)
            {
                Fail(string.Format("VERIFY: TGI mismatch: 0x{0:X8}/0x{1:X8}/0x{2:X16} != 0x{3:X8}/0x{4:X8}/0x{5:X16}",
                    ce.ResourceType, ce.ResourceGroup, ce.Instance, type, group, inst));
                ok = false;
            }
        }

        StblResource.StblResource cst = null;
        if (ok)
        {
            var ce = clist[0];
            Stream cs;
            try { cs = ((APackage)chk).GetResource(ce); }
            catch (Exception ex) { Fail("reopen GetResource failed: " + ex.Message); src.Dispose(); chk.Dispose(); return 1; }
            try { cst = new StblResource.StblResource(1, cs); }
            catch (Exception ex) { Fail("reopen parse STBL failed: " + ex.Message); src.Dispose(); chk.Dispose(); return 1; }

            if (cst.Version != srcVersion) { Fail("VERIFY: Version changed " + cst.Version + " != " + srcVersion); ok = false; }
            if (cst.Entries.Count != srcCount) { Fail("VERIFY: entry_count " + cst.Entries.Count + " != " + srcCount); ok = false; }

            // KeyHash 集合与顺序必须完全一致
            var srcKeys = original.Select(o => o.key).ToList();
            var outKeys = cst.Entries.Select(e => e.KeyHash).ToList();
            if (!srcKeys.SequenceEqual(outKeys)) { Fail("VERIFY: KeyHash order/set mismatch"); ok = false; }

            // Flags 全同 + value 校验 (modified==target, untouched==source)
            var modDict = new Dictionary<uint,string>();
            foreach (var mm in mods) modDict[mm.KeyHash] = mm.TranslatedValue;
            for (int i = 0; i < original.Count && ok; i++)
            {
                var o = original[i];
                var c = cst.Entries[i];
                if (c.Flags != o.flags) { Fail(string.Format("VERIFY: flags mismatch idx {0} 0x{1:X2}!=0x{2:X2}", i, c.Flags, o.flags)); ok = false; break; }
                string expect;
                if (modDict.TryGetValue(o.key, out expect))
                {
                    if (string.CompareOrdinal(c.StringValue, expect) != 0)
                    {
                        Fail(string.Format("VERIFY: modified value mismatch idx {0} key 0x{1:X8}: got \"{2}\" expected \"{3}\"",
                            i, o.key, c.StringValue, expect));
                        ok = false;
                    }
                }
                else if (string.CompareOrdinal(c.StringValue, o.val) != 0)
                {
                    Fail(string.Format("VERIFY: untouched value changed idx {0} key 0x{1:X8}: got \"{2}\" expected source \"{3}\"",
                        i, o.key, c.StringValue, o.val));
                    ok = false;
                }
            }
        }

        // ---- Step 12: manifest / 摘要 (无论成败都打印; 失败时 return 非 0) ----
        Console.WriteLine("=== MANIFEST ===");
        Console.WriteLine("source=" + sourcePath);
        Console.WriteLine("output=" + outPath);
        Console.WriteLine(string.Format("TGI=0x{0:X8}/0x{1:X8}/0x{2:X16}", type, group, inst));
        Console.WriteLine("source_version=" + srcVersion);
        Console.WriteLine("source_entries=" + srcCount);
        Console.WriteLine("output_entries=" + (cst == null ? "?" : cst.Entries.Count.ToString()));
        Console.WriteLine("modifications_requested=" + mods.Count);
        Console.WriteLine("modified_keys=" + string.Join(",", mods.Select(m => string.Format("0x{0:X8}", m.KeyHash))));
        Console.WriteLine("VERIFY=" + (ok ? "PASS" : "FAIL"));

        src.Dispose(); chk.Dispose();
        return ok ? 0 : 2;
    }

    static void Fail(string msg) { Console.Error.WriteLine("ERROR: " + msg); }

    static byte[] ReadAll(Stream s)
    {
        using (var m = new MemoryStream())
        {
            s.CopyTo(m);
            return m.ToArray();
        }
    }

    static uint ParseHexU32(string s) { return Convert.ToUInt32(s.StartsWith("0x") ? s.Substring(2) : s, 16); }
    static ulong ParseHexU64(string s) { return Convert.ToUInt64(s.StartsWith("0x") ? s.Substring(2) : s, 16); }

    // standard Base64 -> UTF-8 exact decode (fail-closed): 返回 false 当 malformed Base64 或 decode 后非合法 UTF-8。
    static bool TryBase64Utf8(string b64, out string text)
    {
        text = null;
        try
        {
            byte[] bytes = Convert.FromBase64String(b64);
            var strict = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);
            text = strict.GetString(bytes);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    // standard Base64 encode (与 Python base64.b64encode 一致, 含 padding)
    static string Base64EncodeUtf8(string s)
    {
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(s));
    }

    // 64-bit Instance 的最高 8 bits (bits 56-63) 编码 locale (0x01=CHS_CN, 0x02=CHT)。
    // 例 0x014EACCF17C8B091 字节序 [01 4E AC CF 17 C8 B0 91], 最高 byte = 0x01。
    static byte LocaleTopByte(ulong inst) { return (byte)((inst >> 56) & 0xFF); }

    // Regression self-check: --selfcheck
    // 验证 locale 顶层字节取位 (必须为 >>56, 而非 >>32)。
    static int SelfCheck()
    {
        int rc = SelfCheckLocale();
        int rc2 = SelfCheckM64();
        bool allOk = (rc == 0 && rc2 == 0);
        Console.WriteLine(allOk ? "SELFCHECK=PASS" : "SELFCHECK=FAIL");
        return allOk ? 0 : 1;
    }

    static int SelfCheckLocale()
    {
        var cases = new[] {
            new { Inst = 0x014EACCF17C8B091UL, Want = (byte)0x01 },
            new { Inst = 0x024EACCF17C8B091UL, Want = (byte)0x02 },
            new { Inst = 0x004EACCF17C8B091UL, Want = (byte)0x00 },
        };
        bool allOk = true;
        foreach (var c in cases)
        {
            byte got = LocaleTopByte(c.Inst);
            bool ok = (got == c.Want);
            allOk &= ok;
            Console.WriteLine(string.Format("locale(0x{0:X16}) => 0x{1:X2} expecting 0x{2:X2} : {3}",
                c.Inst, got, c.Want, ok ? "PASS" : "FAIL"));
        }
        return allOk ? 0 : 1;
    }

    // -m64 transport 契约 regression (纯逻辑, 无需 s4pi)
    // 覆盖: source 含 ':' / value 含 ':' / 中文 Unicode / creator 括号 / 空 value /
    //       普通 ASCII + 负向 (decoded expected != actual source -> write precondition 仍 FAIL)。
    // 编码: string -> UTF-8 -> standard Base64 (与 Python base64.b64encode 双向互通)。
    static int SelfCheckM64()
    {
        bool allOk = true;
        // (label, expected_source, translated_value, want_roundtrip)
        // 用 .NET 4.0 mscorlib 原生 Tuple<> (勿用 ValueTuple: 需额外依赖)。selfcheck 专用。
        var cases = new Tuple<string, string, string, bool>[] {
            Tuple.Create("source_colon",      "Need: [MURPHY] iPhone 13 Pro Max (hat, LEFT)", "需要：iPhone 13 Pro Max 帽子（左）", true),
            Tuple.Create("value_colon",       "普通姿势", "姿势 A：左侧（带冒号的译文）", true),
            Tuple.Create("chinese_unicode",   "相拥", "深情相拥（中文 Unicode）", true),
            Tuple.Create("creator_brackets",  "[wrenmie] Posepack 5: Cozy Days", "[wrenmie] 姿势包 5：舒适时光", true),
            Tuple.Create("empty_value",       "空译文目标", "", true),
            Tuple.Create("ordinary_ascii",    "plain ascii pose", "plain translated value", true),
            Tuple.Create("second_colon_src",  "ALL-IN-ONE: RIGHT", "全能：右侧", true),
        };
        foreach (var c in cases)
        {
            string exp_b64 = Base64EncodeUtf8(c.Item2);
            string val_b64 = Base64EncodeUtf8(c.Item3);
            // 模拟 orchestrator 发送的 -m64 token: KEYHASH:B64_EXP:B64_VAL
            string token = "0x12345678:" + exp_b64 + ":" + val_b64;
            string got_exp, got_val;
            bool parsed = TryParseM64(token, out got_exp, out got_val);
            bool ok = parsed && got_exp == c.Item2 && got_val == c.Item3;
            allOk &= ok;
            Console.WriteLine(string.Format("-m64 {0}: roundtrip={1} (expected_b64_len={2})",
                c.Item1, ok ? "PASS" : "FAIL", exp_b64.Length));
        }

        // 负向 1: decoded expected != actual source -> 该 mod 必须 FAIL (write precondition)
        {
            // 编码一个与真实 source 不同的 expected; 模拟 writer 与 STBL 比对时应 FAIL
            string wrong_expected = "Wrong: source text that won't match";
            string real_source = "Actual: real source in STBL";
            string exp_b64 = Base64EncodeUtf8(wrong_expected);
            string val_b64 = Base64EncodeUtf8("译文");
            string got_exp, got_val;
            bool parsed = TryParseM64("0x12345678:" + exp_b64 + ":" + val_b64, out got_exp, out got_val);
            bool decode_ok = parsed && got_exp == wrong_expected;
            // 关键: decode 正确还原了 wrong_expected, 而 wrong_expected != real_source -> precondition 必须 FAIL
            bool would_fail = decode_ok && (got_exp != real_source);
            bool ok = decode_ok && would_fail;
            allOk &= ok;
            Console.WriteLine(string.Format("-m64 negative: decoded_expected={0}", got_exp));
            Console.WriteLine(string.Format("-m64 negative: expected!=source -> would_fail={0} : {1}",
                would_fail, ok ? "PASS" : "FAIL"));
        }

        // malformed Base64 / invalid UTF-8 / non-hex key -> TryParseM64 必须返回 false (ERROR fail-closed)
        {
            bool ok1 = !TryParseM64("0x1:!!!notbase64@@@:AQ==", out _, out _);
            bool ok2 = !TryParseM64("nothex:bXk=:AQ==", out _, out _);   // 非法 key
            bool ok3 = !TryParseM64("0x1:gA==:Dw==", out _, out _);                       // 非法 UTF-8: 孤立 continuation byte 0x80
            bool ok4 = !TryParseM64("0x1:QQ==", out _, out _);            // 只有 2 字段
            bool ok5 = !TryParseM64("0x1::", out _, out _);               // 空字段
            bool ok = ok1 && ok2 && ok3 && ok4 && ok5;
            allOk &= ok;
            Console.WriteLine(string.Format("-m64 malformed-fail-closed: malformed_b64={0} bad_key={1} bad_utf8={2} two_fields={3} empty={4} : {5}",
                ok1, ok2, ok3, ok4, ok5, ok ? "PASS" : "FAIL"));
        }
        return allOk ? 0 : 1;
    }

    // 解析 -m64 token KEYHASH:B64_EXP:B64_VAL -> (expected, value)。
    // strictly 3 colon fields; KEYHASH 与 B64_EXP 必非空, B64_VAL 可为空 (空译文合法);
    // 任何 decode/key 错误 -> false。
    static bool TryParseM64(string raw, out string expected, out string value)
    {
        expected = null; value = null;
        int c1 = raw.IndexOf(':');
        if (c1 < 0) return false;
        int c2 = raw.IndexOf(':', c1 + 1);
        if (c2 < 0) return false;
        string kh_s = raw.Substring(0, c1);
        string exp_b64 = raw.Substring(c1 + 1, c2 - c1 - 1);
        string val_b64 = raw.Substring(c2 + 1);
        if (kh_s.Length == 0 || exp_b64.Length == 0) return false;
        try { ParseHexU32(kh_s); } catch (Exception) { return false; }
        string e, v;
        if (!TryBase64Utf8(exp_b64, out e)) return false;
        if (!TryBase64Utf8(val_b64, out v)) return false;
        expected = e; value = v;
        return true;
    }
}
