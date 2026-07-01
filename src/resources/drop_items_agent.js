'use strict';
// ============================================================================
// TBHStargaze — Frida 注入脚本 (agent)
//   只读内存，提前读取本关卡已预生成的开箱掉落队列。
//
// 设计（hook 思路 + 针对游戏更新/性能的加固）：
//   · IL2CPP 接口按【符号名】从 GameAssembly.dll 导出表解析，不再硬编码地址。
//   · 不依赖被混淆的方法名：hook vw 的实例方法，从调用中捕获“当前生效”的
//     vw 实例(this)；切场景/掉落会自动跟随到最新实例（避免读到过期旧实例）。
//   · 性能保护：运行时自动剔除“每帧调用”的高频方法 hook（它们会拉低帧率），
//     只保留低频的“事件型”方法用于捕获实例。
//   稳定锚点：类名 vw、掉落字典 vw+0x10、itemId = BoxData+0x3C、
//            EBoxType: 0=普通 1=首领 2=ACT。
// ============================================================================

function sendMsg(type, data) {
    var msg = { type: type };
    if (data) { for (var k in data) msg[k] = data[k]; }
    send(JSON.stringify(msg));
}
function cstr(s) { return Memory.allocUtf8String(s); }
function cR(a) { try { return a && !a.isNull() && Process.findRangeByAddress(a) !== null; } catch (e) { return false; } }
function rP(b, o) { try { var v = b.add(o).readPointer(); return cR(v) ? v : null; } catch (e) { return null; } }
function rI(b, o) { try { return b.add(o).readS32(); } catch (e) { return null; } }

var MOD = Process.enumerateModules().find(function (m) {
    return m.name.toLowerCase().indexOf('gameassembly') !== -1;
});
if (!MOD) {
    sendMsg('error', { msg: '未找到 GameAssembly.dll，请确认游戏已运行' });
} else {
    var EX = {};
    MOD.enumerateExports().forEach(function (e) { EX[e.name] = e.address; });
    function NF(name, ret, args) {
        if (!EX[name]) { sendMsg('error', { msg: '缺少 IL2CPP 导出: ' + name }); return null; }
        return new NativeFunction(EX[name], ret, args);
    }

    var il2cpp_domain_get = NF('il2cpp_domain_get', 'pointer', []);
    var il2cpp_domain_get_assemblies = NF('il2cpp_domain_get_assemblies', 'pointer', ['pointer', 'pointer']);
    var il2cpp_assembly_get_image = NF('il2cpp_assembly_get_image', 'pointer', ['pointer']);
    var il2cpp_class_get_methods = NF('il2cpp_class_get_methods', 'pointer', ['pointer', 'pointer']);
    var il2cpp_method_is_instance = NF('il2cpp_method_is_instance', 'int', ['pointer']);
    var il2cpp_image_get_class_count = NF('il2cpp_image_get_class_count', 'int', ['pointer']);
    var il2cpp_image_get_class = NF('il2cpp_image_get_class', 'pointer', ['pointer', 'int']);
    var il2cpp_class_get_fields = NF('il2cpp_class_get_fields', 'pointer', ['pointer', 'pointer']);
    var il2cpp_field_get_type = NF('il2cpp_field_get_type', 'pointer', ['pointer']);
    var il2cpp_field_get_offset = NF('il2cpp_field_get_offset', 'int', ['pointer']);
    var il2cpp_field_get_name = NF('il2cpp_field_get_name', 'pointer', ['pointer']);
    var il2cpp_type_get_name = NF('il2cpp_type_get_name', 'pointer', ['pointer']);
    var il2cpp_class_from_name = NF('il2cpp_class_from_name', 'pointer', ['pointer', 'pointer', 'pointer']);
    var il2cpp_method_get_name = NF('il2cpp_method_get_name', 'pointer', ['pointer']);
    var il2cpp_method_get_return_type = NF('il2cpp_method_get_return_type', 'pointer', ['pointer']);

    if (!il2cpp_domain_get || !il2cpp_class_get_methods || !il2cpp_method_is_instance ||
        !il2cpp_image_get_class_count || !il2cpp_image_get_class || !il2cpp_class_get_fields ||
        !il2cpp_field_get_type || !il2cpp_type_get_name) {
        sendMsg('error', { msg: 'IL2CPP 接口解析失败，可能游戏版本不兼容' });
    } else {
        // ---- 按【字段签名】动态定位掉落数据类 ----
        // 该类有 Dictionary<EBoxType, List<BoxData>> 字段。类名会随版本被混淆(vw→vy→…)，
        // 但 BoxData / EBoxType 的类型名稳定，故按字段类型签名查找，免疫类名变化。
        // 同时动态取该字段的偏移作为 OFF_DICT，免疫字段偏移变化。
        var K = null;
        var OFF_DICT = 0x10;
        (function () {
            function rcsLocal(p) { try { return p.isNull() ? null : p.readCString(); } catch (e) { return null; } }
            var dom = il2cpp_domain_get();
            var sizePtr = Memory.alloc(4);
            var asms = il2cpp_domain_get_assemblies(dom, sizePtr);
            var n = sizePtr.readU32();
            for (var a = 0; a < n && !K; a++) {
                var asm = asms.add(a * Process.pointerSize).readPointer();
                if (!asm || asm.isNull()) continue;
                var img = il2cpp_assembly_get_image(asm);
                if (!img || img.isNull()) continue;
                var cc = il2cpp_image_get_class_count(img);
                if (cc <= 0 || cc > 30000) continue;
                for (var c = 0; c < cc; c++) {
                    var k = il2cpp_image_get_class(img, c);
                    if (!k || k.isNull()) continue;
                    var it = Memory.alloc(Process.pointerSize);
                    var f, off = -1;
                    while (!(f = il2cpp_class_get_fields(k, it)).isNull()) {
                        var tn = rcsLocal(il2cpp_type_get_name(il2cpp_field_get_type(f)));
                        if (tn && tn.indexOf('Dictionary') >= 0 && tn.indexOf('EBoxType') >= 0 &&
                            tn.indexOf('List') >= 0 && tn.indexOf('BoxData') >= 0) {
                            off = il2cpp_field_get_offset(f);
                            break;
                        }
                    }
                    if (off >= 0) { K = k; OFF_DICT = off; break; }
                }
            }
        })();

        if (!K) {
            sendMsg('error', { msg: '未找到掉落数据类，可能游戏版本不匹配' });
        } else {
            sendMsg('diag', { msg: '已定位掉落数据类（字段偏移 0x' + OFF_DICT.toString(16) + '）' });

            var MAX_ITEMS = 64;

            // ---- 解析 BoxData.o_rewardItemId 偏移 + ObscuredInt 解密函数 ----
            // 新版把字段加密成 ObscuredInt（明文 0x3C 已没）。真正的掉落物品 = o_rewardItemId
            // （箱内奖励）。调用游戏自带的 ObscuredInt → int 解密(op_Implicit，运算符名稳定不混淆)
            // 来读取。物品 ID 本身没变，item.json 仍可用。
            function rcs2(p) { try { return p.isNull() ? null : p.readCString(); } catch (e) { return null; } }
            function findClassByName(ns, name) {
                var dom = il2cpp_domain_get(); var sp = Memory.alloc(4);
                var asms = il2cpp_domain_get_assemblies(dom, sp); var n = sp.readU32();
                var nsp = cstr(ns), np = cstr(name);
                for (var a = 0; a < n; a++) {
                    var asm = asms.add(a * Process.pointerSize).readPointer(); if (!asm || asm.isNull()) continue;
                    var img = il2cpp_assembly_get_image(asm); if (!img || img.isNull()) continue;
                    var k = il2cpp_class_from_name(img, nsp, np);
                    if (k && !k.isNull()) return k;
                }
                return null;
            }
            // o_rewardItemId 偏移（动态；回退 0x30）
            var OFF_REWARD = 0x30;
            (function () {
                var bd = findClassByName('TaskbarHero', 'BoxData'); if (!bd) return;
                var it = Memory.alloc(Process.pointerSize); var f;
                while (!(f = il2cpp_class_get_fields(bd, it)).isNull()) {
                    if (rcs2(il2cpp_field_get_name(f)) === 'o_rewardItemId') { OFF_REWARD = il2cpp_field_get_offset(f); break; }
                }
            })();
            // ObscuredInt → int 解密函数（op_Implicit, static, ret Int32）
            var DECRYPT = null;
            (function () {
                var oi = findClassByName('CodeStage.AntiCheat.ObscuredTypes', 'ObscuredInt'); if (!oi) return;
                var it = Memory.alloc(Process.pointerSize); var mm;
                while (!(mm = il2cpp_class_get_methods(oi, it)).isNull()) {
                    if (il2cpp_method_is_instance(mm)) continue;
                    if (rcs2(il2cpp_method_get_name(mm)) !== 'op_Implicit') continue;
                    if (rcs2(il2cpp_type_get_name(il2cpp_method_get_return_type(mm))) !== 'System.Int32') continue;
                    var code = mm.readPointer();
                    if (cR(code)) { try { DECRYPT = new NativeFunction(code, 'int', ['pointer']); } catch (e) {} }
                    break;
                }
            })();
            sendMsg('diag', { msg: 'o_rewardItemId@0x' + OFF_REWARD.toString(16) + '，解密函数=' + (DECRYPT ? '已就绪' : '缺失(回退明文)') });

            // 取单个 BoxData 的真实物品 id：优先解密 o_rewardItemId；回退旧版明文 0x3C
            function readItemId(bd) {
                if (DECRYPT) { try { var v = DECRYPT(bd.add(OFF_REWARD)); if (v && v > 0) return v; } catch (e) {} }
                try { var p = rI(bd, 0x3C); if (p) return p; } catch (e) {}
                return null;
            }

            // 对象头(前8字节)必须等于该类指针。注意：类指针 K 可能不在可枚举内存区间，
            // 因此这里直接读原始字节与 K 比较，不能用会按区间过滤的 rP()。
            function headerOk(vw) {
                try { return cR(vw) && vw.readPointer().equals(K); } catch (e) { return false; }
            }

            // 结构校验：只用安全的内存读，不调用 DECRYPT —— 用于扫描/校验候选实例，
            // 绝不能在未确认(可能是垃圾)的指针上执行游戏的解密函数（会崩/卡死）。
            function structOk(vw) {
                if (!vw || !headerOk(vw)) return false;
                var dict = rP(vw, OFF_DICT); if (!dict) return false;
                var count = rI(dict, 0x20); if (count === null || count < 1 || count > 8) return false;
                var ep = rP(dict, 0x18); if (!ep) return false;
                var total = 0;
                for (var i = 0; i < count; i++) {
                    var entry = ep.add(0x20 + i * 24);
                    var key = rI(entry, 0x08);
                    var lp = rP(entry, 0x10);
                    if (key === null || key < 0 || key > 5 || !lp) return false;
                    var arr = rP(lp, 0x10);
                    var sz = rI(lp, 0x18);
                    if (!arr || sz === null || sz < 0 || sz > 500) return false;
                    total += sz;
                }
                return total > 0;
            }

            // 完整读取并解密（仅对已确认的实例调用）
            function readQueues(vw) {
                if (!structOk(vw)) return null;
                var dict = rP(vw, OFF_DICT);
                var count = rI(dict, 0x20);
                var ep = rP(dict, 0x18);
                var res = [];
                for (var i = 0; i < count; i++) {
                    var entry = ep.add(0x20 + i * 24);
                    var key = rI(entry, 0x08);
                    var lp = rP(entry, 0x10);
                    var arr = rP(lp, 0x10);
                    var sz = rI(lp, 0x18);
                    var ids = [];
                    for (var j = 0; j < Math.min(sz, MAX_ITEMS); j++) {
                        var bd = rP(arr, 0x20 + j * 8);
                        if (!bd) continue;
                        var id = readItemId(bd);
                        if (id !== null) ids.push(id);
                    }
                    res.push({ eboxType: key, items: ids });
                }
                return res;
            }

            // ===== 状态 =====
            var g_vw = null;
            var g_lastKey = '';
            var g_lastFirst;
            var g_boxOpenCount = 0;
            var g_firstSeen = false;
            var g_waitTicks = 0;

            function snapKey(qs) {
                var p = [];
                for (var i = 0; i < qs.length; i++) p.push(qs[i].eboxType + ':' + qs[i].items.length + ':' + (qs[i].items[0] || 0));
                return p.join('|');
            }
            function emitIfChanged(source) {
                if (!g_vw) return;
                var qs = readQueues(g_vw);
                if (!qs || qs.length === 0) return;
                var key = snapKey(qs);
                if (key === g_lastKey) return;
                g_lastKey = key;
                var normal = [], boss = [], act = [];
                for (var i = 0; i < qs.length; i++) {
                    if (qs[i].eboxType === 0) normal = qs[i].items;
                    else if (qs[i].eboxType === 1) boss = qs[i].items;
                    else if (qs[i].eboxType === 2) act = qs[i].items;
                }
                if (g_lastFirst !== undefined && normal.length && g_lastFirst !== normal[0]) {
                    g_boxOpenCount++;
                    sendMsg('box_open', { count: g_boxOpenCount });
                }
                g_lastFirst = normal.length ? normal[0] : undefined;
                send(JSON.stringify({ type: 'queue', source: source, normal: normal, boss: boss, act: act }));
            }

            // ---- 纯异步堆扫描定位实例（完全不 hook，零钩子开销，游戏不卡）----
            // 掉落数据实例是常驻单例（地址固定、原地更新内容）。因此不需要任何 Interceptor
            // hook —— 启动时异步扫描锁定一次，之后轮询读取即可看到掉落/切等级带来的内容变化。
            // 不 hook = 玩家点背包/符文/魔方等任何操作都不会经过我们的代码，彻底消除卡顿。
            // Memory.scan 为异步分块执行，扫描期间也不会冻结游戏。
            var PAT = (function () {
                var hx = K.toString(16); while (hx.length < 16) hx = '0' + hx;
                var bb = []; for (var i = 0; i < 8; i++) bb.push(hx.substr((7 - i) * 2, 2));
                return bb.join(' ');
            })();
            var g_scanning = false;
            function scanRange(base, size) {
                return new Promise(function (resolve) {
                    var hits = [];
                    try {
                        Memory.scan(base, size, PAT, {
                            onMatch: function (addr) { hits.push(addr); },
                            onComplete: function () { resolve(hits); },
                            onError: function () { resolve(hits); }
                        });
                    } catch (e) { resolve(hits); }
                });
            }
            async function scanForInstance() {
                if (g_scanning) return;
                g_scanning = true;
                try {
                    var ranges = Process.enumerateRanges('rw-');
                    for (var r = 0; r < ranges.length; r++) {
                        if (g_vw && structOk(g_vw)) return;     // 已捕获
                        var hits = await scanRange(ranges[r].base, ranges[r].size);
                        for (var i = 0; i < hits.length; i++) {
                            if (headerOk(hits[i]) && structOk(hits[i])) { g_vw = hits[i]; return; }
                        }
                    }
                } catch (e) { } finally { g_scanning = false; }
            }
            scanForInstance();   // 启动即扫描，无需等待掉落

            // 轮询：跟随 g_vw 读取，队列变化时刷新；丢失则异步重新扫描
            setInterval(function () {
                try {
                    if (!g_vw || !structOk(g_vw)) {
                        g_vw = null;
                        scanForInstance();
                        if (!g_firstSeen && (++g_waitTicks % 5) === 0)
                            sendMsg('diag', { msg: '正在定位掉落数据…进入有箱子的关卡即可显示' });
                        return;
                    }
                    if (!g_firstSeen) g_firstSeen = true;
                    emitIfChanged('poll');
                } catch (e) {}
            }, 800);

            sendMsg('ready', {});
        }
    }
}
