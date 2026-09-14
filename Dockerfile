# Trailhead Rx web. Session 5 points Fly.io or Render at this file.
# The corpus documents and the index are NOT in the image (public repo rule);
# they are mounted or built on the host. See README "Run the website".
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "trailheadrx.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
