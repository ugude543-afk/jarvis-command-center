FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir flask requests google-genai gunicorn
COPY . .
EXPOSE 10000
CMD ["python", "app.py"]
