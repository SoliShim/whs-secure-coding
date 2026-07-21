# 제출용 보고서 생성

공개 저장소에는 개인정보가 없는 PDF 생성기만 포함한다. 실제 제출본은
`output/pdf/`에 생성되며 `.gitignore`로 추적하지 않는다.

```bash
python -m pip install -r requirements-dev.txt
python scripts/generate_report.py \
  --class-name 01반 \
  --name 홍길동 \
  --phone-last4 1234 \
  --repo-url https://github.com/USERNAME/whs-secure-coding
```

생성 파일명은 자동으로
`[WHS][secure-coding][01반]홍길동(1234).pdf`가 된다. 공백은 모두 제거된다.
실제 이름, 전화번호 뒷자리, 저장소 소유자는 최종 생성 시점에만
인자로 전달한다.

보고서는 로컬에 있는 `output/screenshots/`의 검증 이미지를 사용할 수 있다.
이미지가 없는 clean clone에서도 이미지 섹션만 생략하고 PDF를 생성한다.
