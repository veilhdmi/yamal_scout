# Scheduled refresh job (Cloud Run Job): ingest_espn.py + dbt build. Code is pulled from GitHub at run time.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY refresh_job/requirements.txt /job/requirements.txt
RUN pip install -r /job/requirements.txt
COPY refresh_job/run.py /job/run.py

RUN useradd --create-home jobuser
USER jobuser
WORKDIR /home/jobuser

CMD ["python", "/job/run.py"]
