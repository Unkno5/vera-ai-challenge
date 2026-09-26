FROM python:3.12-slim
RUN useradd -m -u 1000 user
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user vera ./vera
USER user
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn vera.app:app --host 0.0.0.0 --port ${PORT}"]
