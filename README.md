---
title: OfficeFlow Backend
emoji: 🏢
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
license: mit
---

# OfficeFlow AI Attendance Backend

FastAPI backend for OfficeFlow attendance app with face recognition, geofencing, and smart attendance features.

## Features
- 🔐 JWT Authentication
- 👤 Face Recognition (1:N search with DeepFace)
- 📍 Geofencing validation
- 📶 WiFi signal strength verification
- 🔒 Device binding security
- 📊 Attendance analytics

## API Endpoints
- `GET /` - Health check
- `POST /register` - User registration
- `POST /login` - User login
- `POST /smart-attendance` - Smart check-in/check-out
- `GET /logs/{email}` - Attendance history
- `GET /analytics/{email}` - Work hour analytics

## Tech Stack
- FastAPI
- MongoDB Atlas
- DeepFace (face recognition)
- OpenCV
