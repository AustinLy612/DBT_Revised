FROM python:3.12-slim

WORKDIR /app

# Use Alibaba Cloud mirrors for faster downloads in China
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

# System dependencies for WeasyPrint and other tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    libcairo2 \
    fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn -r requirements.txt

COPY . .

RUN mkdir -p /app/logs /app/staticfiles
RUN python manage.py collectstatic --noinput || true

EXPOSE 8000

CMD ["gunicorn", "dbt_platform.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
