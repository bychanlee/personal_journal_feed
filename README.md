# Journal Digest

저널 목록으로부터 신규 논문을 매일 가져와 Claude Haiku로 연구 관심사 기반 점수를 매기고,
정적 HTML로 생성하여 GitHub Pages에 배포.

## Setup

1. `config.yaml`에서 피드 목록과 연구 프로필 수정
2. GitHub repo Settings → Secrets에 `ANTHROPIC_API_KEY` 추가
3. Settings → Pages → Source를 **GitHub Actions**로 설정
4. GitHub Actions가 매일 06:00 KST에 자동 실행

## Local

```bash
ANTHROPIC_API_KEY=sk-... python generate.py
open index.html
```
