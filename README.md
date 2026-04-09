# FeedFlux

![Status](https://img.shields.io/badge/status-under%20development-FEA362) ![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg) ![Python](https://img.shields.io/badge/python-3.10+-blue.svg) ![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi) ![Next.js](https://img.shields.io/badge/Next.js-333333?logo=next.js&logoColor=white)

FeedFlux is an email assistant that summarizes emails/newsletters and lets users ask questions about their inbox. It is built on a RAG pipeline using Gemini embeddings and ChromaDB, served via a FastAPI backend and Next.js frontend.

## Tech Stack

- **Backend:** Python, FastAPI, SQLAlchemy
- **Frontend:** Next.js, React, Tailwind CSS, shadcn/ui
- **AI & Data:** Google Generative AI (Gemini API), ChromaDB, MSAL (Microsoft Authentication)
- **Deployment:** Docker, Docker Compose