# Cold Mail Tracker Docker Setup

This document provides instructions for setting up the Cold Mail application using Docker and configuring email tracking with a transparent pixel.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- A domain name that points to your server (for email tracking)

## Getting Started

1. Clone the repository
2. Create a `.env` file in the root directory with the following variables:

```
# MongoDB connection URI (this is already set up in docker-compose)
MONGO_URI=mongodb://mongo:27017/cold_mail_db

# Secret key for session management (change this in production)
SECRET_KEY=your_secret_key_change_this_in_production

# Gemini API key (if using)
GEMINI_API_KEY=your_gemini_api_key_here

# Public URL for tracking pixel (used in emails)
# Must be accessible from the internet for email tracking to work
PUBLIC_BASE_URL=https://your-domain.com/
```

3. Build and start the Docker containers:

```bash
docker-compose up -d
```

4. Access the application at http://localhost:8000

## Email Tracking

The application uses a 1x1 transparent pixel to track when recipients open emails. Here's how it works:

1. When an email is sent, a unique tracking ID is generated for each recipient
2. A transparent pixel image is embedded in the email, with a URL pointing to your server: `https://your-domain.com/track/{tracking_id}.png`
3. When the recipient opens the email, their email client requests the image
4. The server logs this request and marks the email as opened in the database
5. You can view tracking statistics in the application

### Important Notes for Email Tracking

- The `PUBLIC_BASE_URL` environment variable must be set to a publicly accessible URL
- Make sure your domain has proper DNS records
- If using a firewall, ensure port 80/443 is open for incoming HTTP/HTTPS traffic
- Consider setting up HTTPS with a valid SSL certificate for security

## Accessing the MongoDB Database

You can access the MongoDB database directly at `localhost:27017`. The data is persisted in a Docker volume named `mongo_data`.

## Stopping the Application

To stop the Docker containers:

```bash
docker-compose down
```

To completely remove the containers and volumes:

```bash
docker-compose down -v
```

## Troubleshooting

- If email tracking is not working, verify that your `PUBLIC_BASE_URL` is accessible from the internet
- Check the application logs with `docker-compose logs app`
- For MongoDB issues, check `docker-compose logs mongo` 