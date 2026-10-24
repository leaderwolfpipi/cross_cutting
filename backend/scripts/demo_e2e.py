"""End-to-end demo over the HTTP API: import footage → analyse → multi-round creative remix + dedup → one-click
publish (抖音/快手 against the TEST-ONLY mock platform with injected faults, 视频号/小红书 via the assisted channel).

Start the stack first (scripts/run_demo.sh does all of this), then:
    python scripts/demo_e2e.py --api http://127.0.0.1:8010 --examples ../../examples --mock http://127.0.0.1:9100
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

PLATFORMS = ["douyin", "kuaishou", "channels", "xiaohongshu"]


class Api:
    def __init__(self, base: str, key: str):
        self.c = httpx.Client(base_url=base, headers={"X-API-Key": key}, timeout=120, trust_env=False)

    def __getattr__(self, method):
        def call(path, **kw):
            r = self.c.request(method.upper(), path, **kw)
            if r.status_code >= 400:
                raise SystemExit(f"{method.upper()} {path} → {r.status_code}: {r.text[:500]}")
            return r.json()
        return call


def wait(pred, what: str, timeout: float = 3600, every: float = 3.0):
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        ok, msg = pred()
        if msg != last:
            print(f"  [{time.time() - t0:6.0f}s] {what}: {msg}", flush=True)
            last = msg
        if ok:
            return
        time.sleep(every)
    raise SystemExit(f"timeout waiting for {what}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8010")
    ap.add_argument("--key", default="louhaokey")
    ap.add_argument("--examples", required=True)
    ap.add_argument("--mock", default="http://127.0.0.1:9100")
    ap.add_argument("--per-platform", type=int, default=2)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--report", default="demo_report.json")
    a = ap.parse_args()
    api = Api(a.api, a.key)
    report: dict = {"rounds": [], "publish": []}

    print("== 0. 系统信息")
    info = api.get("/api/system/info")
    print(f"  渲染后端: {info['render_backends']}  HDR: {info['hdr_tonemap']}  文案: {info['copywriter']}")
    report["system"] = info

    print("== 1. 导入素材（服务器本地导入，按文件夹名识别产品）")
    res = api.post("/api/materials/import", json={"paths": [str(Path(a.examples).resolve())], "license_type": "self_shot"})
    print(f"  新导入 {len(res['created'])} 个，跳过 {len(res['skipped'])} 个")
    mats = api.get("/api/materials")

    def analysed():
        ms = api.get("/api/materials")
        st = {}
        for m in ms:
            st[m["status"]] = st.get(m["status"], 0) + 1
        return all(m["status"] in ("ready", "failed") for m in ms), json.dumps(st, ensure_ascii=False)
    wait(analysed, "素材分析")
    mats = [m for m in api.get("/api/materials") if m["status"] == "ready"]
    groups = {m["source_group"] for m in mats}
    print(f"  可用素材 {len(mats)} 个，来源分组 {len(groups)} 个，镜头 {sum(m['shot_count'] for m in mats)} 个，"
          f"HDR {sum(1 for m in mats if m['hdr'])} 个")
    report["materials"] = [{k: m[k] for k in ("filename", "product", "duration", "width", "height", "fps", "hdr",
                                               "shot_count", "source_group")} for m in mats]

    print("== 2. 账号（抖音/快手 → 本地模拟开放平台；视频号/小红书 → 人工辅助）")
    existing = {(x["platform"], x["mode"]) for x in api.get("/api/accounts")}
    for p, mode, tok, oid in [("douyin", "api", "mock-douyin-token", "dy_open_1"),
                              ("kuaishou", "api", "mock-kuaishou-token", "ks_open_1"),
                              ("channels", "assisted", "", ""), ("xiaohongshu", "assisted", "", "")]:
        if (p, mode) not in existing:
            api.post("/api/accounts", json={"platform": p, "mode": mode, "display_name": f"渔港码头·{p}",
                                            "access_token": tok, "refresh_token": tok.replace("token", "refresh"),
                                            "open_id": oid, "expires_in": 86400 * 7, "min_interval_s": 0,
                                            "daily_limit": 50})

    print("== 3. 创建混剪项目")
    proj = api.post("/api/projects", json={
        "name": f"今日到港 · {time.strftime('%m%d %H:%M')}",
        "product": {"name": "海鲜", "items": sorted({m["product"] for m in mats}),
                    "selling_points": ["当天到港", "鲜活现捞", "支持现场加工"], "store": "渔港海鲜码头", "city": "厦门"},
        "material_ids": [m["id"] for m in mats], "platforms": PLATFORMS, "options": {"audio_mode": "original"}})
    pid = proj["id"]

    for rnd in range(1, a.rounds + 1):
        print(f"== 4.{rnd} 第 {rnd} 轮生成：每个平台 {a.per_platform} 条")
        t0 = time.time()
        api.post(f"/api/projects/{pid}/generate", json={"count": a.per_platform})

        def generated():
            vs = api.get(f"/api/variants?project_id={pid}")
            st = {}
            for v in vs:
                st[v["status"]] = st.get(v["status"], 0) + 1
            busy = any(v["status"] in ("queued", "planning", "rendering", "checking") for v in vs)
            return not busy, json.dumps(st, ensure_ascii=False)
        wait(generated, "生成+查重")
        vs = api.get(f"/api/variants?project_id={pid}")
        rows = []
        for v in sorted(vs, key=lambda x: (x["platform"], x["seq"])):
            full = api.get(f"/api/variants/{v['id']}")
            rep = full.get("dedup_report") or {}
            top = (rep.get("matches") or [{}])[0]
            l1 = (full.get("plan_report") or {}).get("l1", {})
            rows.append({"seq": v["seq"], "platform": v["platform"], "template": v["template_id"], "status": v["status"],
                         "duration": v["duration"], "backend": v["render_backend"], "render_s": v["render_seconds"],
                         "hook": (v["copy"] or {}).get("hook", "").replace("\n", " / "),
                         "title": (v["copy"] or {}).get("title", ""),
                         "L1_plan_max_pair": l1.get("max_pair"), "attempts": len((full.get("plan_report") or {}).get("attempts", [])),
                         "P": top.get("P"), "V": top.get("V"), "A": top.get("A"),
                         "library": (rep.get("metrics") or {}).get("library"), "compared": rep.get("compared"),
                         "video": v["video_url"], "error": v["error"]})
        new = [r for r in rows if r["seq"] > (rnd - 1) * a.per_platform * len(PLATFORMS)]
        print(f"  用时 {time.time() - t0:.0f}s")
        print(f"  {'#':>3} {'平台':<12}{'模板':<16}{'状态':<9}{'时长':>6} {'P':>5} {'V':>5} {'A':>5} {'库':>5}  钩子")
        for r in new:
            f = lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else "  - "  # noqa: E731
            print(f"  {r['seq']:>3} {r['platform']:<12}{r['template']:<16}{r['status']:<9}{r['duration']:>6.1f} "
                  f"{f(r['P']):>5} {f(r['V']):>5} {f(r['A']):>5} {f(r['library']):>5}  {r['hook']}")
            if r["error"]:
                print(f"      ! {r['error'][:200]}")
        report["rounds"].append({"round": rnd, "variants": new, "seconds": round(time.time() - t0)})

    print("== 5. 一键发布（注入故障：抖音创建接口丢失响应一次、快手上传 500 两次）")
    mock = httpx.Client(base_url=a.mock, timeout=10, trust_env=False)
    mock.post("/__faults", json={"rules": [{"path": "create_video", "kind": "lost_response", "times": 1},
                                           {"path": "api/upload", "kind": "error500", "times": 2}]})
    pre = api.post("/api/publish/preflight", json={"project_id": pid})
    for t in pre["targets"]:
        bad = [c["message"] for c in t["checks"] if not c["ok"]]
        print(f"  预检 {t['platform']:<12} {'通过' if t['ok'] else '阻断'} {'；'.join(bad)}")
    batch = api.post("/api/publish/one-click", json={"project_id": pid, "skip_failed": True})
    print(f"  批次 {batch['id'][:8]} 状态 {batch['status']}，任务 {len(batch['deliveries'])} 个")

    def published():
        b = api.get(f"/api/publish/batches/{batch['id']}")
        st = {d["platform"]: d["state"] for d in b["deliveries"]}
        done = all(s in ("published", "awaiting_operator", "failed", "needs_auth", "platform_rejected") for s in st.values())
        return done, json.dumps(st, ensure_ascii=False)
    wait(published, "发布状态", timeout=900, every=2)

    print("== 6. 人工辅助通道：通过手机交接页确认视频号/小红书")
    b = api.get(f"/api/publish/batches/{batch['id']}")
    for d in b["deliveries"]:
        if d["state"] != "awaiting_operator":
            continue
        full = api.get(f"/api/publish/deliveries/{d['id']}")
        mobile = full["handoff"]["mobile_url"]
        u = urlparse(mobile)
        page = httpx.get(f"{a.api}{u.path}?{u.query}", trust_env=False)
        assert page.status_code == 200 and "保存视频" in page.text
        token = parse_qs(u.query)["t"][0]
        r = httpx.post(f"{a.api}{u.path}/confirm?t={token}", json={"post_url": f"https://example.invalid/{d['platform']}/post",
                                                                    "operator": "运营小王"}, trust_env=False)
        print(f"  {d['platform']:<12} 交接页 OK，确认结果 {r.status_code}")

    b = api.get(f"/api/publish/batches/{batch['id']}")
    print(f"== 7. 结果：批次 {b['status']}")
    for d in b["deliveries"]:
        full = api.get(f"/api/publish/deliveries/{d['id']}")
        trail = " → ".join(e["to"] for e in full["events"])
        print(f"  {d['platform']:<12} {d['state']:<10} 重试 {d['attempts']}  {d['share_url']}")
        print(f"      轨迹: {trail}")
        report["publish"].append({"platform": d["platform"], "state": d["state"], "attempts": d["attempts"],
                                  "share_url": d["share_url"], "trail": [e["to"] + "：" + e["message"] for e in full["events"]]})
    state = mock.get("/__state").json()
    counts = {k: len(v) for k, v in state["posts"].items()}
    print(f"  模拟平台作品数（每个平台应为 1，无重复）: {counts}")
    report["mock_posts"] = counts
    report["batch_status"] = b["status"]
    report["project_id"] = pid
    Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"== 报告已写入 {a.report}")
    if any(c > 1 for c in counts.values()):
        sys.exit("duplicate posts detected!")


if __name__ == "__main__":
    main()
