# Personal Journal Feed — Setup Guide

## GitHub

- Repo: `bychanlee/personal_journal_feed` (public)
- Pages: `https://bychanlee.github.io/personal_journal_feed/` (source: `gh-pages` branch)

## URL 구조

- 최신: `https://bychanlee.github.io/personal_journal_feed/` (→ 리다이렉트)
- 날짜별: `https://bychanlee.github.io/personal_journal_feed/YYYY/MM/YYYY-MM-DD.html`
- 예시: `https://bychanlee.github.io/personal_journal_feed/2026/03/2026-03-17.html`

## 레포 구조 (gh-pages 브랜치)

```
personal_journal_feed/
├── index.html              # 최신 날짜로 리다이렉트
├── .nojekyll
└── 2026/
    └── 03/
        ├── 2026-03-10.html
        ├── 2026-03-11.html
        └── ...
```

## 레포 구조 (main 브랜치)

```
personal_journal_feed/
├── generate.py             # fetch → Haiku score → HTML
├── config.yaml             # 피드 목록 + 연구 프로필
├── requirements.txt
├── README.md
├── SETUP.md                # 이 파일
├── latest.json             # 최근 스코어링 결과
└── scripts/
    └── run-digest.sh       # 로컬 LaunchAgent가 매일 실행
```

## Morning Brief 연동

**Slack DM에 추가할 내용 (매일):**
```
📄 *Personal Journal Feed*
<https://bychanlee.github.io/personal_journal_feed/YYYY/MM/YYYY-MM-DD.html|Open today's feed>
```

**Obsidian 데일리 노트에 추가할 섹션** (`### ✅ Routines` 위):
```markdown
### 📄 Journal Feed
> [Open today's feed](https://bychanlee.github.io/personal_journal_feed/YYYY/MM/YYYY-MM-DD.html)
```

Claude Cowork scheduled task `morning-brief`의 프롬프트에 위 내용을 포함시키세요.
날짜는 실행 시점 기준으로 동적 생성되어야 합니다.

## 수동 실행

```bash
cd ~/Projects/_infras/personal_journal_feed
./scripts/run-digest.sh
```

## 점수 기준

- 5 = 내 연구 영역에 직접 해당, 반드시 읽어야 함
- 4 = 밀접하게 관련, 유용할 가능성 높음
- 3 = 어느 정도 관련, 훑어볼 만함
- 2 = 간접적으로 관련
- 1 = 관련 없음
