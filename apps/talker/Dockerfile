FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY apps/talker/requirements.txt /app/apps/talker/requirements.txt
RUN pip install -r /app/apps/talker/requirements.txt
# Warm the Smart Turn + Silero models into the image so the first call doesn't pay for loading them.
RUN python -c "from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3 as A; A()" \
 && python -c "from pipecat.audio.vad.silero import SileroVADAnalyzer as V; V()"
# LiveKit's end-of-turn model (has the caller finished, from their words), pinned by hash (eou.py).
COPY apps/talker/eou_fetch.py /tmp/eou_fetch.py
RUN python /tmp/eou_fetch.py /opt/eou && rm /tmp/eou_fetch.py
COPY protocol /app/protocol
COPY apps/__init__.py /app/apps/__init__.py
COPY apps/talker /app/apps/talker
EXPOSE 8090
# No access log and uvicorn at WARNING: uvicorn logs WebSocket URLs (with the stream key) at INFO via its error
# logger, which --no-access-log alone does not cover. App logs go through loguru.
CMD ["uvicorn", "apps.talker.server:app", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--log-level", "warning"]
