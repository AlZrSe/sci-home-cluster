# Scientific Home Cluster Backend

This is the backend component of the Scientific Home Cluster project, providing a FastAPI-based API server for managing scientific workloads on a home GPU cluster.

## Project Structure

```
backend/
├── backend/              # Python package
│   ├── __init__.py
│   ├── main.py           # FastAPI application entry point
│   ├── api/              # API route definitions (versioned)
│   │   ├── __init__.py
│   │   └── v1/           # API version 1
│   │       ├── __init__.py
│   │       ├── jobs.py
│   │       ├── nodes.py
│   │       └── auth.py
│   ├── core/             # Core configuration, security, utilities
│   │   ├── __init__.py
│   │   ├── config.py     # Pydantic settings
│   │   ├── security.py   # Authentication utilities
│   │   └── utils.py
│   ├── models/           # Pydantic models
│   │   └── __init__.py
│   └── services/         # Business logic layer
│       ├── __init__.py
│       ├── job_service.py
│       ├── node_service.py
│       └── auth_service.py
├── tests/                # Backend-specific tests
│   ├── __init__.py
│   ├── unit/
│   └── integration/
├── pyproject.toml        # Project metadata and dependencies
├── README.md             # This file
└── .env.example          # Example environment file
```

## Setup Instructions

### Prerequisites
- Python 3.9 or higher
- Git

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/AlZrSe/sci-home-cluster.git
   cd sci-home-cluster
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Unix or MacOS:
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -e ./backend
   ```

4. Set up environment variables:
   ```bash
   cp backend/.env.example backend/.env
   # Edit backend/.env as needed
   ```

5. Run the development server:
   ```bash
   uvicorn backend.backend.main:app --reload
   ```

## API Documentation

Once the server is running, you can view the API documentation at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## License

This project is licensed under the MIT License.