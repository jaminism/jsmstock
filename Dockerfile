# NAS(Container Manager) 등 서버 환경에서 데일리 스캐너(scripts/local/daily_watchlist.py)를
# 상시 실행하기 위한 런타임 이미지. 소스 코드(src/, scripts/)는 이미지에 굽지 않고 컨테이너
# 실행 시 리포지토리 디렉터리를 /app 에 바인드 마운트하는 것을 전제로 한다 — 코드/개인 스크립트를
# 바꿀 때마다 이미지를 다시 빌드할 필요 없이 파일만 다시 배포하면 되도록 하기 위함.
#
# 빌드:
#   docker build -t rich-stock-scanner .
# 실행 (리포 루트를 /app 에 마운트, PYTHONPATH로 src/ 를 인식시킴):
#   docker run --rm -v /path/to/rich_stock:/app rich-stock-scanner \
#       python scripts/local/daily_watchlist.py

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python"]
