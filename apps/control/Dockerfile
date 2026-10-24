FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# PDF shaping (Pango + HarfBuzz) and Noto fonts for every Indian script, same as CI.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
      fonts-noto-core fonts-noto-extra \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY apps/control/requirements.txt /app/apps/control/requirements.txt
RUN pip install -r /app/apps/control/requirements.txt
COPY packages /app/packages
COPY protocol /app/protocol
COPY apps/__init__.py /app/apps/__init__.py
COPY apps/thinker /app/apps/thinker
# talker's pure logic (claim patterns) is used by the call report; no Pipecat in this image
COPY apps/talker/__init__.py /app/apps/talker/__init__.py
COPY apps/talker/logic /app/apps/talker/logic
COPY apps/control /app/apps/control
ENV PYTHONPATH=/app/packages:/app
EXPOSE 8091
# No access log, uvicorn at WARNING: /api/d/<token> URLs are bearer links and must never reach logs.
CMD ["uvicorn", "apps.control.service:app", "--host", "0.0.0.0", "--port", "8091", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--log-level", "warning"]
