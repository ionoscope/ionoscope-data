FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin ionoscope

COPY requirements.txt ./requirements.txt
COPY data_preparation/requirements.txt ./data_preparation/requirements.txt
COPY feature_engineering/requirements.txt ./feature_engineering/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip install --no-cache-dir -r data_preparation/requirements.txt \
    && python -m pip install --no-cache-dir -r feature_engineering/requirements.txt

COPY --chown=ionoscope:ionoscope . .
RUN mkdir -p live_data data cleaned normalized features update_tasks \
    && chown ionoscope:ionoscope /app live_data data cleaned normalized features update_tasks

USER ionoscope

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8001"]
