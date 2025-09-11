FROM python:3.11
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["flask", "--app", "app:create_app", "run", "--host=0.0.0.0"]