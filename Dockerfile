FROM python:3.11-bullseye

ENV PYTHONBUFFERED=1

WORKDIR /usr/src/app

COPY ./requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000