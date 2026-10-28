# The FlowSME server. Customers' apps (FlowSME Recorder) talk to it over HTTPS; put it behind a TLS proxy or a
# platform that terminates HTTPS (Azure App Service, Render, Fly.io, Railway...).
#
#   docker build -t flowsme-server .
#   docker run -p 8765:8765 -v flowsme-data:/data -e MODEL_API_KEY=... -e FLOWSME_SERVER_URL=https://your.domain flowsme-server
#   docker exec -it <container> python -m flowsme.server create-org "Acme" --seats 5 --manager "Dana Lee"
#   docker cp <container>:/tmp/seat-files-to-send .     (send the seat file privately, then delete both copies)
FROM python:3.12-slim
WORKDIR /app
COPY requirements-server.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements-server.txt
COPY flowsme ./flowsme
RUN rm -rf flowsme/client flowsme/app.py flowsme/settings_ui.py flowsme/admin_ui.py flowsme/recorder.py
ENV FLOWSME_DATA=/data FLOWSME_SEAT_OUT=/tmp/seat-files-to-send PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown nobody /data
VOLUME /data
EXPOSE 8765
USER nobody
CMD ["python", "-m", "flowsme.server", "serve", "--host", "0.0.0.0", "--port", "8765"]
