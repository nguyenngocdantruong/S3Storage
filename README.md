# Video S3 Player

A Flask-based web application to browse, stream, upload, and manage videos/files stored in AWS S3 or S3-compatible object storage.

## Features

- **Multi-user Management**: Role-based access control (Admin and User) with storage quotas.
- **S3-Compatible Integration**: Connects to AWS S3, Cloudflare R2, MinIO, or any other S3-compatible service.
- **Dynamic File Browser**: Browse, upload, download, and delete files directly through the web interface.
- **Video & Document Viewer**: Stream videos and view files directly in the browser.
- **Real-Time Progress Tracking**: Visual upload and operation progress bars.
- **Persistent Logs**: Built-in system logging to keep track of operations and errors.

---

## Configuration

The application reads initial admin configuration from `config.conf` in the project root directory.

Example `config.conf`:
```ini
[ADMIN]
username=admin
password=your_secure_password
fullname=Administrator
dob=2000-01-01
email=admin@example.com
```

---

## Running with Docker (Recommended)

To run the application inside a Docker container using Gunicorn:

### Prerequisites
- Docker installed
- Docker Compose installed (optional, but highly recommended)

### Method 1: Using Docker Compose
Before running the container, ensure the persistent files exist on your host so Docker doesn't mistakenly create them as directories during bind mounting:

```bash
# On Linux/macOS:
touch s3player.db .secret_key system.log

# On Windows (PowerShell):
New-Item -ItemType File s3player.db, .secret_key, system.log -Force
```

To build and start the service:
```bash
docker compose up -d --build
```
The application will be accessible at: **`http://localhost:7090`** (binding to `0.0.0.0:7090` inside the container).

To stop the container:
```bash
docker compose down
```

### Method 2: Using Docker CLI Only
If you prefer running with raw Docker commands:

1. Build the Docker image:
   ```bash
   docker build -t video-s3-player .
   ```

2. Run the Docker container:
   ```bash
   docker run -d -p 7090:7090 --name video-s3-player-container video-s3-player
   ```

---

## Running Locally (Development Mode)

If you wish to run the project locally without Docker:

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Application**:
   ```bash
   python app.py
   ```
   The Flask development server will start on `http://127.0.0.1:7090`.
