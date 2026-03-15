FROM python:3.8-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY index.html .

# Copy favicon assets
COPY favicon.svg .
COPY favicon.ico .

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 5000

# Run Flask app with unbuffered output
CMD ["python", "-u", "app.py"]
