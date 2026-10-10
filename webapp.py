# ============================================================
# webapp.py  —  Mini App + API cho bot (chạy cùng bot)
# ============================================================
import os, hmac, hashlib, json, urllib.parse, datetime
import requests
from flask import Flask, request, jsonify, Response

from bot import (BOT_TOKEN, COIN, TREE_PLANT_COST, TREE_STAGES, TREE_HARVEST_REWARD,
                 TREE_WATER_COOLDOWN_H, MIN_WITHDRAW, WITHDRAW_METHODS, REFERRAL_BONUS,
                 REFERRAL_PERCENT, TASK_MIN_WAIT, connect, _now, get_user,
                 get_or_create_user, task_pending_for, get_task, complete_task,
                 plant_tree, water_tree, harvest_tree, create_withdrawal,
                 list_channels, claim_channel_reward)

PORT = int(os.environ.get("PORT", "8080"))
BOT_USERNAME = None


def _ensure_tables():
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS task_opens (user_id INTEGER NOT NULL, "
                    "task_id INTEGER NOT NULL, opened_at TEXT NOT NULL, PRIMARY KEY(user_id,task_id))")


def record_task_open(uid, tid):
    with connect() as con:
        con.execute("INSERT INTO task_opens(user_id,task_id,opened_at) VALUES(?,?,?) "
                    "ON CONFLICT(user_id,task_id) DO UPDATE SET opened_at=excluded.opened_at", (uid, tid, _now()))


def get_task_open(uid, tid):
    with connect() as con:
        r = con.execute("SELECT opened_at FROM task_opens WHERE user_id=? AND task_id=?", (uid, tid)).fetchone()
        return r["opened_at"] if r else None


MINIAPP_HTML = r"""<!doctype html>
<html lang="vi"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<title>Nông Trại Kiếm Tiền</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:#f4f6f8;--card:#fff;--txt:#1f2937;--muted:#6b7280;--grn:#22c55e;--grn2:#16a34a;--line:#e6e8eb}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:var(--bg);color:var(--txt)}
header{padding:16px;background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff}
header h1{margin:0;font-size:17px}.bal{font-size:28px;font-weight:700;margin-top:6px}
.tabs{display:flex;position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid var(--line)}
.tabs button{flex:1;padding:10px 2px;border:0;background:none;color:var(--muted);font-size:10px}
.tabs button.on{color:var(--grn2);font-weight:700}
main{padding:12px 12px 84px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
.card h2{margin:0 0 8px;font-size:15px}.muted{color:var(--muted);font-size:13px}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--line)}.row:last-child{border-bottom:0}
button.act{background:var(--grn);color:#fff;border:0;border-radius:10px;padding:11px;font-weight:600;width:100%;margin-top:8px;font-size:14px}
button.act.sec{background:#e5e7eb;color:#111}a.link{color:var(--grn2);word-break:break-all}
.bar{letter-spacing:2px;font-size:18px;margin:6px 0}
input,select{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;margin-top:8px;font-size:14px}
.badge{background:#dcfce7;color:#166534;border-radius:999px;padding:2px 8px;font-size:12px}
</style></head><body>
<header><h1>🌱 Nông Trại Kiếm Tiền</h1><div class="bal" id="balance">…</div><div class="muted" id="who"></div></header>
<main id="main"></main>
<nav class="tabs">
<button data-t="acc" class="on">👤 Tài khoản</button><button data-t="task">🔗 Nhiệm vụ</button>
<button data-t="tree">🌳 Cây</button><button data-t="invite">👥 Mời bạn</button><button data-t="wd">💰 Rút</button>
</nav>
<script>
var tg = window.Telegram ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); }
var INIT = tg ? tg.initData : "";
var main = document.getElementById("main");
var ME = null, CFG = null;
function api(path, body){
  return fetch(path, {method: body ? "POST" : "GET",
    headers: {"X-Init-Data": INIT, "Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined}).then(function(r){ return r.json(); });
}
function coins(n){ return Number(n).toLocaleString("vi-VN"); }
function boot(){
  api("/api/me").then(function(r){
    if (r.error){ main.innerHTML = "<div class='card'>⚠️ " + r.error + "</div>"; return; }
    ME = r.user; CFG = r.config;
    document.getElementById("balance").textContent = coins(ME.coins) + " " + CFG.coin;
    document.getElementById("who").textContent = "Tổng kiếm: " + coins(ME.total_earned) + " " + CFG.coin;
    show("acc");
  });
}
function show(t){
  document.querySelectorAll(".tabs button").forEach(function(b){ b.classList.toggle("on", b.dataset.t === t); });
  if (t === "acc") renderAcc(); else if (t === "task") renderTask();
  else if (t === "tree") renderTree(); else if (t === "invite") renderInvite(); else renderWd();
}
function renderAcc(){
  main.innerHTML = "<div class='card'><h2>Tài khoản</h2>" +
   "<div class='row'><span>ID</span><b>" + ME.user_id + "</b></div>" +
   "<div class='row'><span>Số dư</span><b>" + coins(ME.coins) + " " + CFG.coin + "</b></div>" +
   "<div class='row'><span>Tổng kiếm</span><b>" + coins(ME.total_earned) + " " + CFG.coin + "</b></div>" +
   "<div class='row'><span>Đã mời</span><b>" + ME.ref_count + " người</b></div>" +
   "<div class='row'><span>Cây</span><b>" + (ME.tree_stage < 0 ? "Chưa trồng" : "Cấp " + ME.tree_stage + "/" + CFG.tree_stages) + "</b></div></div>";
}
function renderTask(){
  api("/api/tasks").then(function(r){
    if (!r.tasks || !r.tasks.length){ main.innerHTML = "<div class='card'>🎉 Bạn đã hoàn thành hết nhiệm vụ!</div>"; return; }
    main.innerHTML = r.tasks.map(function(t){ return "<div class='card'><h2>" + t.title + "</h2>" +
      "<div class='muted'>Thưởng: <span class='badge'>+" + coins(t.reward) + " " + CFG.coin + "</span></div>" +
      "<a class='link' href='" + t.url + "' target='_blank' onclick='openTask(" + t.id + ")'>🔗 Mở link nhiệm vụ</a>" +
      "<button class='act' onclick='doneTask(" + t.id + ")'>✅ Tôi đã hoàn thành</button></div>"; }).join("");
  });
}
function openTask(id){ api("/api/tasks/" + id + "/open", {}); }
function doneTask(id){ api("/api/tasks/" + id + "/done", {}).then(function(r){
  if (r.error) return alert(r.error);
  alert("+ " + coins(r.reward) + " " + CFG.coin + " 🎉"); refresh(); show("task"); }); }
function renderTree(){ api("/api/tree").then(function(r){
  var s = r.stage, body;
  if (s < 0) body = "<div class='muted'>Bạn chưa có cây.</div><button class='act' onclick=\"tree('plant')\">🌱 Trồng cây (" + coins(CFG.tree_plant_cost) + " " + CFG.coin + ")</button>";
  else if (s >= CFG.tree_stages) body = "<div class='bar'>" + "🟩".repeat(CFG.tree_stages) + "</div><div class='muted'>Cây đã trưởng thành!</div><button class='act' onclick=\"tree('harvest')\">🎁 Thu hoạch (+" + coins(CFG.tree_harvest_reward) + ")</button>";
  else body = "<div class='bar'>" + "🟩".repeat(s) + "⬜".repeat(CFG.tree_stages - s) + "</div><div class='muted'>Cấp " + s + "/" + CFG.tree_stages + ". Tưới mỗi " + CFG.water_cooldown_h + "h.</div><button class='act' onclick=\"tree('water')\">💧 Tưới nước</button>";
  main.innerHTML = "<div class='card'><h2>Cây của bạn</h2>" + body + "</div>"; }); }
function tree(a){ api("/api/tree/" + a, {}).then(function(r){
  if (r.error) alert(r.error); else if (r.reward) alert("+ " + coins(r.reward) + " " + CFG.coin);
  refresh(); show("tree"); }); }
function renderInvite(){ api("/api/invite").then(function(r){
  main.innerHTML = "<div class='card'><h2>Mời bạn bè</h2>" +
   "<div class='muted'>Mỗi người: +" + coins(CFG.referral_bonus) + " " + CFG.coin + " · Hoa hồng " + CFG.referral_percent + "%</div>" +
   "<div class='row'><span>Đã mời</span><b>" + r.ref_count + "</b></div>" +
   "<p><a class='link' href='" + r.link + "' target='_blank'>" + r.link + "</a></p>" +
   "<button class='act' onclick=\"navigator.clipboard.writeText('" + r.link + "');alert('Đã copy link')\">📋 Copy link</button>" +
   "<button class='act sec' onclick=\"window.open('https://t.me/share/url?url=" + encodeURIComponent(r.link) + "','_blank')\">📤 Chia sẻ</button></div>"; }); }
function renderWd(){
  var opts = Object.keys(CFG.withdraw_methods).map(function(k){ return "<option value='" + k + "'>" + CFG.withdraw_methods[k] + "</option>"; }).join("");
  main.innerHTML = "<div class='card'><h2>Rút tiền</h2>" +
   "<div class='muted'>Tối thiểu " + coins(CFG.min_withdraw) + " " + CFG.coin + ". Số dư: " + coins(ME.coins) + "</div>" +
   "<select id='wmethod'>" + opts + "</select>" +
   "<input id='wamount' type='number' placeholder='Số " + CFG.coin + " muốn rút'/>" +
   "<input id='waccount' placeholder='Thông tin nhận tiền (số MoMo / STK + tên)'/>" +
   "<button class='act' onclick='doWd()'>Gửi yêu cầu rút</button></div>";
}
function doWd(){
  var method = document.getElementById("wmethod").value;
  var amount = Number(document.getElementById("wamount").value);
  var account = document.getElementById("waccount").value;
  api("/api/withdraw", {amount: amount, method: method, account: account}).then(function(r){
    if (r.error) return alert(r.error);
    alert("✅ Đã gửi yêu cầu rút #" + r.id + ". Chờ admin duyệt."); refresh(); show("acc"); });
}
function refresh(){ api("/api/me").then(function(r){ if (!r.error){ ME = r.user; document.getElementById("balance").textContent = coins(ME.coins) + " " + r.config.coin; } }); }
document.querySelectorAll(".tabs button").forEach(function(b){ b.onclick = function(){ show(b.dataset.t); }; });
boot();
</script></body></html>"""


def validate_init_data(init_data, token=None):
    token = token or BOT_TOKEN
    if not init_data:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received = pairs.pop("hash", None)
    if not received:
        return None
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    user = None
    if "user" in pairs:
        try:
            user = json.loads(pairs["user"])
        except Exception:
            user = None
    return {"user": user, "raw": pairs}


def _req_uid():
    init_data = request.headers.get("X-Init-Data")
    if not init_data and request.method == "POST":
        init_data = (request.get_json(silent=True) or {}).get("initData")
    v = validate_init_data(init_data)
    return v["user"]["id"] if (v and v.get("user")) else None


def _tg_is_member(chat_id, user_id):
    try:
        j = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
                         params={"chat_id": chat_id, "user_id": user_id}, timeout=10).json()
        return bool(j.get("ok")) and j["result"].get("status") in ("member", "administrator", "creator", "restricted")
    except Exception:
        return None


def create_web_app():
    web = Flask(__name__)

    def cfg():
        return {"coin": COIN, "tree_plant_cost": TREE_PLANT_COST, "tree_stages": TREE_STAGES,
                "tree_harvest_reward": TREE_HARVEST_REWARD, "water_cooldown_h": TREE_WATER_COOLDOWN_H,
                "min_withdraw": MIN_WITHDRAW, "withdraw_methods": WITHDRAW_METHODS,
                "referral_bonus": REFERRAL_BONUS, "referral_percent": REFERRAL_PERCENT,
                "task_min_wait": TASK_MIN_WAIT, "bot_username": BOT_USERNAME}

    @web.get("/")
    @web.get("/app")
    def app_page():
        return Response(MINIAPP_HTML, mimetype="text/html")

    @web.get("/health")
    def health():
        return {"ok": True}

    @web.post("/api/me")
    def api_me():
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        u, _ = get_or_create_user(uid, None, None)
        return jsonify(user={k: u[k] for k in ("user_id", "coins", "total_earned", "ref_count", "tree_stage")}, config=cfg())

    @web.get("/api/tasks")
    def api_tasks():
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        return jsonify(tasks=[{k: t[k] for k in ("id", "title", "url", "reward")} for t in task_pending_for(uid)])

    @web.post("/api/tasks/<int:tid>/open")
    def api_task_open(tid):
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        if not get_task(tid):
            return jsonify(error="Nhiệm vụ không tồn tại."), 404
        record_task_open(uid, tid)
        return jsonify(ok=True, wait=TASK_MIN_WAIT)

    @web.post("/api/tasks/<int:tid>/done")
    def api_task_done(tid):
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        opened = get_task_open(uid, tid)
        if not opened:
            return jsonify(error="Hãy mở link nhiệm vụ trước."), 400
        elapsed = (datetime.datetime.utcnow() - datetime.datetime.fromisoformat(opened)).total_seconds()
        if elapsed < TASK_MIN_WAIT:
            return jsonify(error=f"Đợi thêm {int(TASK_MIN_WAIT - elapsed)}s nữa."), 429
        reward = complete_task(uid, tid)
        if reward == -1:
            return jsonify(error="Bạn đã hoàn thành nhiệm vụ này rồi."), 409
        return jsonify(reward=reward)

    @web.get("/api/tree")
    def api_tree():
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        return jsonify(stage=(get_user(uid) or {}).get("tree_stage", -1))

    @web.post("/api/tree/<action>")
    def api_tree_action(action):
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        if action == "plant":
            ok, msg = plant_tree(uid)
        elif action == "water":
            ok, msg, _ = water_tree(uid)
        elif action == "harvest":
            ok, msg = harvest_tree(uid)
        else:
            return jsonify(error="Hành động không hợp lệ."), 400
        if not ok:
            return jsonify(error=msg), 400
        out = {"ok": True, "msg": msg}
        if action == "harvest":
            out["reward"] = TREE_HARVEST_REWARD
        return jsonify(**out)

    @web.get("/api/invite")
    def api_invite():
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        u, _ = get_or_create_user(uid, None, None)
        return jsonify(link=(f"https://t.me/{BOT_USERNAME}?start=ref_{uid}" if BOT_USERNAME else ""), ref_count=u["ref_count"])

    @web.post("/api/withdraw")
    def api_withdraw():
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        body = request.get_json(silent=True) or {}
        method = body.get("method", "bank")
        ok, res = create_withdrawal(uid, int(body.get("amount", 0)), WITHDRAW_METHODS.get(method, method),
                                    str(body.get("account", "")))
        return (jsonify(id=res) if ok else (jsonify(error=res), 400))

    @web.get("/api/channels")
    def api_channels():
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        return jsonify(channels=[{k: c[k] for k in ("id", "title", "invite_url", "reward")} for c in list_channels()])

    @web.post("/api/channels/<int:cid>/check")
    def api_channel_check(cid):
        uid = _req_uid()
        if not uid:
            return jsonify(error="unauthorized"), 401
        ch = next((c for c in list_channels() if c["id"] == cid), None)
        if not ch:
            return jsonify(error="Kênh không tồn tại."), 404
        if _tg_is_member(ch["chat_id"], uid) is False:
            return jsonify(error="Bạn chưa tham gia kênh này."), 403
        reward = claim_channel_reward(uid, cid)
        return (jsonify(reward=reward) if reward else (jsonify(error="Bạn đã nhận thưởng kênh này rồi."), 409))

    return web


def run_web():
    global BOT_USERNAME
    _ensure_tables()
    try:
        j = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10).json()
        if j.get("ok"):
            BOT_USERNAME = j["result"]["username"]
    except Exception as e:
        print("getMe:", e)
    create_web_app().run(host="0.0.0.0", port=PORT, threaded=True)


print("✅ webapp.py sẵn sàng — create_web_app() + run_web()")
