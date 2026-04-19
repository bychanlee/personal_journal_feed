# Journal Digest

저널 목록으로부터 신규 논문을 매일 가져와 Claude Haiku로 연구 관심사 기반 점수를 매기고,
정적 HTML로 생성하여 GitHub Pages에 배포.

로컬 Mac의 LaunchAgent가 `scripts/run-digest.sh`를 실행하여
Claude Code CLI(`claude --print --model haiku`)로 스코어링한다.

## Setup

1. `config.yaml`에서 피드 목록과 연구 프로필 수정
2. Settings → Pages → Source를 **Deploy from a branch** → `gh-pages` / `/ (root)`로 설정

## Local

```bash
python generate.py
open index.html
```
