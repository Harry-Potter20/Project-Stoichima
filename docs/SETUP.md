# Setup Guide

## Prerequisites
- Python 3.11+
- A free API key from [football-data.org](https://www.football-data.org/)

## Installation

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
# Edit .env and add your FOOTBALL_DATA_API_KEY
```

## Run the API

```bash
uvicorn app.main:app --reload
# Visit http://localhost:8000/docs
```
