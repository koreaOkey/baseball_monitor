# baseball_monitor

KBO 라이브 시청자 수를 모니터링해서 Telegram으로 알림을 보내는 단발성(single-shot) 스크립트.
openclaw cron이 10분마다 `run.sh`를 호출한다.

## 동작
- Supabase `public.games`(LIVE 경기) + `public.device_tokens`를 조인해 경기별 시청자 수를 집계
- 경기 없음: 출력 없음 (`NO_CHANGE_SKIP`)
- 경기 시작/진행/종료: 매 tick마다 Telegram 알림 전송
- 상태는 `state.json`에 저장 (git-ignored)

## 설정
비밀키는 환경변수로 주입한다 (코드에 하드코딩하지 않음).

```bash
cp .env.example .env   # 값 채우기 (.env 는 git-ignored)
```

| 변수 | 설명 |
|------|------|
| `SUPABASE_PROJECT` | Supabase 프로젝트 ref |
| `SUPABASE_TOKEN`   | Supabase 관리 API 토큰 (`sbp_...`) — **필수** |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 — **필수** |
| `TELEGRAM_CHAT_ID`   | 알림 받을 chat id |

## 실행
```bash
./run.sh                # .env 로드 후 1회 실행
python3 monitor.py      # 직접 실행 (환경변수 필요)
```

## cron
openclaw cron 잡(`kbo-monitor`)이 10분 간격 isolated 세션에서 `~/kbo-monitor/run.sh`를 실행한다.
