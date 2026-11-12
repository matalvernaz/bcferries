FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Write log lines as they happen. Buffered, they reached `docker logs` in batches hours late,
# all stamped with the moment of the flush, which hid when a fetch had really failed.
ENV PYTHONUNBUFFERED=1

EXPOSE 4000

CMD ["python", "app.py"]
