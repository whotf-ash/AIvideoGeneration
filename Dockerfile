FROM python:3.10-slim

# Install system dependencies including FFmpeg and fonts for Pillow
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Run the bot
CMD ["python", "main.py"]