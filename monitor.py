#!/usr/bin/env python3
"""
KBO Live Viewer Monitor (single-shot mode)
- openclaw cron이 주기적으로 호출
- 경기 없음: 출력 없음 (NO_CHANGE_SKIP)
- 경기 시작: 알림 시작, 이후 매 tick마다 출력
- 경기 중: 매번 출력 (변화 없어도)
- 경기 종료: 종료 알림 후 IDLE 복귀
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ── Config ──────────────────────────────────────────────────────
# Secrets come from the environment (see .env.example). run.sh sources a
# local, git-ignored .env so nothing sensitive ever lands in the repo.
def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: missing required env var {name} (set it in .env)", file=sys.stderr)
        sys.exit(2)
    return val

SUPABASE_PROJECT = os.environ.get("SUPABASE_PROJECT", "snrafqoqpmtoannnnwdq")
SUPABASE_TOKEN = _require_env("SUPABASE_TOKEN")

TELEGRAM_BOT_TOKEN = _require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6549327158")

STATE_FILE = Path(os.environ.get("STATE_FILE", os.path.expanduser("~/kbo-monitor/state.json")))
KST = timezone(timedelta(hours=9))

QUERY = """\
SELECT g.id AS game_id,
       g.away_team || ' @ ' || g.home_team AS matchup,
       g.inning,
       g.away_score || ':' || g.home_score AS score,
       g.away_score::int AS away_score_n,
       g.home_score::int AS home_score_n,
       COUNT(d.*) FILTER (WHERE d.platform='ios') AS ios_viewers,
       COUNT(d.*) FILTER (WHERE d.platform='watchos') AS watch_viewers,
       COUNT(d.*) FILTER (WHERE d.platform='ios' AND d.updated_at > NOW() - INTERVAL '15 minutes') AS active_ios_15min
FROM public.games g
LEFT JOIN public.device_tokens d ON d.game_id = g.id
WHERE g.status = 'LIVE'
GROUP BY g.id, g.home_team, g.away_team, g.inning, g.home_score, g.away_score
ORDER BY ios_viewers DESC;\
"""

# ── Supabase Query ──────────────────────────────────────────────

def query_supabase() -> list[dict]:
    url = f"https://api.supabase.com/v1/projects/{SUPABASE_PROJECT}/database/query"
    body = json.dumps({"query": QUERY}).encode()
    req = Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {SUPABASE_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "kbo-monitor/1.0",
    })
    try:
        try:
            resp = urlopen(req, timeout=15)
        except HTTPError as e:
            if e.code == 201:
                resp = e
            else:
                raise
        data = json.loads(resp.read())
        for row in data:
            for k in ("ios_viewers", "watch_viewers", "active_ios_15min", "away_score_n", "home_score_n"):
                row[k] = int(row.get(k, 0) or 0)
        return data
    except (URLError, json.JSONDecodeError, KeyError) as e:
        print(f"ERROR: Supabase query failed: {e}", file=sys.stderr)
        return []

# ── State Persistence ───────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

# ── Message Formatting ──────────────────────────────────────────

def format_live_message(prev_games: dict, curr_games: list[dict], tick: int, start_iso: str, started: bool) -> str:
    start_time = datetime.fromisoformat(start_iso)
    elapsed_min = int((datetime.now(KST) - start_time).total_seconds() / 60)

    curr_ids = set(str(g["game_id"]) for g in curr_games)
    prev_ids = set(prev_games.keys())

    total_now = sum(g["ios_viewers"] for g in curr_games)
    total_prev = sum(g["ios_viewers"] for g in prev_games.values())
    diff_total = total_now - total_prev
    diff_str = f"+{diff_total}" if diff_total >= 0 else str(diff_total)

    # Header
    if started:
        lines = [f"⚾ KBO LIVE (Tick {tick})"]
    else:
        lines = [f"⚾ KBO LIVE (Tick {tick} · +{elapsed_min}분)"]
    lines.append(f"👥 총 관람자 {total_now}명 (직전 대비 {diff_str})")
    lines.append("")

    # Ended games
    ended_ids = prev_ids - curr_ids
    for gid in ended_ids:
        g = prev_games[gid]
        away, home = g["matchup"].split(" @ ")
        lines.append(f"🏁 {away} vs {home} · {g.get('inning','')} · {g['score']} · 종료 (-{g['ios_viewers']}명)")
    if ended_ids:
        lines.append("")

    # New games (mid-session)
    new_ids = curr_ids - prev_ids
    for g in curr_games:
        if str(g["game_id"]) in new_ids and not started:
            away, home = g["matchup"].split(" @ ")
            lines.append(f"🆕 {away} vs {home} · {g['inning']} · {g['score']} · NEW")
    if new_ids and not started:
        lines.append("")

    # Game list
    for g in curr_games:
        gid = str(g["game_id"])
        prev_v = prev_games.get(gid, {}).get("ios_viewers", 0)
        d = g["ios_viewers"] - prev_v
        d_str = f"＋{d}" if d > 0 else str(d)
        away, home = g["matchup"].split(" @ ")
        lines.append(f"• {away} vs {home} · {g['inning']} · {g['score']} · {g['ios_viewers']}명 ({d_str})")

    # Summary
    obs = build_observations(prev_games, curr_games, diff_total, ended_ids)
    if obs:
        lines.append("")
        lines.append(f"📌 요약: {obs}")

    return "\n".join(lines)


def format_ended_message(prev_games: dict, start_iso: str, tick: int, peak: int) -> str:
    start_time = datetime.fromisoformat(start_iso)
    elapsed_min = int((datetime.now(KST) - start_time).total_seconds() / 60)
    total = sum(g["ios_viewers"] for g in prev_games.values())

    lines = [
        f"⚾ KBO LIVE 종료 (Tick {tick} · +{elapsed_min}분)",
        "",
    ]
    for g in prev_games.values():
        away, home = g["matchup"].split(" @ ")
        lines.append(f"🏁 {away} vs {home} · {g.get('inning','')} · {g['score']} · {g['ios_viewers']}명")

    lines.append("")
    lines.append(f"📌 최종: 동시 시청 {total}명 · 피크 {peak}명")
    return "\n".join(lines)


def build_observations(prev_games: dict, curr_games: list[dict], diff_total: int, ended_ids: set) -> str:
    parts = []

    if diff_total > 5:
        parts.append(f"전체 유입 급증({diff_total:+d})")
    elif diff_total > 0:
        parts.append(f"전체 유입 소폭 증가({diff_total:+d})")
    elif diff_total == 0:
        parts.append("전체 유지")
    elif diff_total > -5:
        parts.append(f"전체 소폭 감소({diff_total:+d})")
    else:
        parts.append(f"전체 이탈({diff_total:+d})")

    close = [g for g in curr_games if abs(g["away_score_n"] - g["home_score_n"]) <= 2]
    if close:
        parts.append(f"접전 {len(close)}경기 유지")

    movers = []
    for g in curr_games:
        gid = str(g["game_id"])
        prev_v = prev_games.get(gid, {}).get("ios_viewers", 0)
        d = g["ios_viewers"] - prev_v
        if abs(d) >= 3:
            movers.append((g["matchup"], d))
    for name, d in movers:
        parts.append(f"{name} 관람자 {d:+d}")

    if ended_ids:
        parts.append(f"{len(ended_ids)}경기 종료")

    return ", ".join(parts) if parts else ""

# ── Telegram Direct Send ────────────────────────────────────────

def send_telegram(message: str):
    """Telegram Bot API로 직접 전송 (announce 우회)"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }).encode()
    req = Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
    })
    try:
        resp = urlopen(req, timeout=10)
        print(f"Telegram sent ({len(message)} chars)")
    except (URLError, HTTPError) as e:
        print(f"Telegram send error: {e}", file=sys.stderr)

# ── Main (single-shot) ──────────────────────────────────────────

def main():
    state = load_state()
    games = query_supabase()

    prev_games = state.get("prev_games", {})
    was_live = state.get("had_live", False)
    is_live = bool(games)

    # ── No games, wasn't live → silent skip ──
    if not is_live and not was_live:
        print("NO_CHANGE_SKIP")
        return

    # ── Games just started (IDLE → LIVE) ──
    started = is_live and not was_live

    if started:
        state["tick"] = 0
        state["start_time"] = datetime.now(KST).isoformat()
        state["peak_viewers"] = 0

    # ── Games are live → always report ──
    if is_live:
        state["tick"] = state.get("tick", 0) + 1
        total = sum(g["ios_viewers"] for g in games)
        state["peak_viewers"] = max(state.get("peak_viewers", 0), total)

        msg = format_live_message(prev_games, games, state["tick"], state["start_time"], started)
        send_telegram(msg)

        state["prev_games"] = {str(g["game_id"]): g for g in games}
        state["had_live"] = True
        save_state(state)

    # ── All games just ended (LIVE → IDLE) ──
    elif was_live and not is_live:
        state["tick"] = state.get("tick", 0) + 1
        peak = state.get("peak_viewers", 0)
        msg = format_ended_message(prev_games, state["start_time"], state["tick"], peak)
        send_telegram(msg)

        save_state({"had_live": False})


if __name__ == "__main__":
    main()
