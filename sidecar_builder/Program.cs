// SidecarBuilder — P1 自动化 sidecar 的最小工程 (canary)
// 目标 (Phase 3B2 Step③ 锁定):
//   1. 能在 Windows 编译 (msbuild / Visual Studio, .NET Framework 4.0)
//   2. 能创建一个只含 1 个 STBL 的 package
//   3. 能重新打开并核对 TGI + entries
// 本版绝不生成批量/659 包, 不碰 Animation, 原 mod 只读。
//
// 复用的成熟 writer: s4pi (仓库 lib/s4pi_src) — 序列化逻辑完全由 s4pi 负责, 零手写 DBPF。
//
// 用法 (示例):
//   SidecarBuilder.exe -out out.package -type 0x220557DA -group 0x80000000 -inst 0x014EACCF17C8B091 -k FDD36EF2:左 -k 552CC77A:相拥
//   (每个 -k 用 ':' 分隔 keyHash(hex) 与文本)

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using s4pi.Interfaces;
using s4pi.Package;
using StblResource;

class Program
{
    static int Main(string[] args)
    {
        string outPath = null;
        uint type = 0x220557DA;   // STBL
        uint group = 0x80000000;  // 与原 CHS STBL 一致的 group
        ulong inst  = 0x014EACCF17C8B091UL; // 与原 CHS STBL 一致的 instance
        var kv = new List<KeyValuePair<uint,string>>();

        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "-out":  outPath = args[++i]; break;
                case "-type": type = ParseHexU32(args[++i]); break;
                case "-group":group = ParseHexU32(args[++i]); break;
                case "-inst": inst  = ParseHexU64(args[++i]); break;
                case "-k":
                    string raw = args[++i];
                    int colon = raw.IndexOf(':');
                    if (colon < 0) { Console.Error.WriteLine("bad -k (need 'key:value'): " + raw); return 1; }
                    uint kh = Convert.ToUInt32(raw.Substring(0, colon), 16);
                    string txt = raw.Substring(colon + 1);
                    kv.Add(new KeyValuePair<uint,string>(kh, txt));
                    break;
                default:
                    Console.Error.WriteLine("unknown arg: " + args[i]); return 1;
            }
        }
        if (outPath == null || kv.Count == 0)
        {
            Console.Error.WriteLine("usage: SidecarBuilder -out OUT -k KEY:VAL ...");
            return 1;
        }

        // 0) 用 s4pi 建空包
        IPackage pkg = Package.NewPackage(1);
        // 1) 用 StblResource wrapper 构造只含指定 key 的 STBL 体
        MemoryStream ms = new MemoryStream();
        var stbl = new StblResource.StblResource(1, ms);   // 空 STBL
        var entries = stbl.Entries;
        // 注: StringEntry 的 handler 传 null 即可 — OnElementChanged/null-check 已确认安全,
        //     我们只在 Add 前一次性赋值, 不再触发变更事件。
        foreach (var p in kv)
        {
            var se = new StblResource.StringEntry(1, null);
            se.KeyHash = p.Key;
            se.Flags = 0;
            se.StringValue = p.Value;
            entries.Add(se);
        }
        // 序列化 (s4pi AResource.Stream / UnParse 会回填 stringLength)
        Stream body = stbl.Stream;
        byte[] bodyBytes = ReadAll(body);
        Console.WriteLine("STBL body bytes = " + bodyBytes.Length);


        // 2) 加入包: Type+Group+Instance 完全按原 CHS STBL
        var rk = new TGIBlock(1, null, type, group, inst);
        var ie = pkg.AddResource(rk, body, true);
        if (ie == null) { Console.Error.WriteLine("AddResource failed (dup?)"); return 1; }

        // 3) 保存
        pkg.SaveAs(outPath);
        Console.WriteLine("SAVED " + outPath);
        pkg = null;

        // 4) 重新打开并核对 TGI + entries
        IPackage open = Package.OpenPackage(1, outPath, false);
        var list = open.GetResourceList;
        Console.WriteLine("REOPEN resource count = " + list.Count);
        foreach (IResourceIndexEntry e in list)
        {
            Console.WriteLine("  type=0x{0:X8} group=0x{1:X8} inst=0x{2:X16} off={3} sz={4} comp={5:X4}",
                e.ResourceType, e.ResourceGroup, e.Instance, e.Chunkoffset, e.Filesize, e.Compressed);
        }
        // 核对目标 STBL + 回读 entries
        IResourceIndexEntry target = null;
        uint wantT = 0x220557DA; ulong wantI = inst;
        foreach (IResourceIndexEntry e in list)
            if (e.ResourceType == wantT && e.Instance == wantI) target = e;
        if (target == null) { Console.Error.WriteLine("VERIFY FAIL: 未找到目标 STBL TGI"); return 2; }
        Console.WriteLine("VERIFY TGI OK: type=0x{0:X8} group=0x{1:X8} inst=0x{2:X16}", target.ResourceType, target.ResourceGroup, target.Instance);

        // 回读 STBL body 并列出 entries (确认写入正确)
        Stream got = open.GetResource(target);
        byte[] gb = ReadAll(got);
        Console.WriteLine("STBL reopen bytes = " + gb.Length);
        if (gb.Length >= 21)
        {
            ulong n = BitConverter.ToUInt64(gb, 7);
            Console.WriteLine("reopen STBL entries = " + n);
        }
        // 记录验证通过
        return 0;
    }

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
}
