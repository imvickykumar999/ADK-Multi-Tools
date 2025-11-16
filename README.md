# Steps to Schedule Zoom Meet

<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/36c2edd4-4d48-4d26-8be1-744b64f1bc5c" />

```bash
# To create folder structure from scratch
adk create --type=code app_name --model gemini-2.5-flash --api_key AIzaS.....GOOGLE_API_KEY

# to run adk web
adk web --session_service_uri sqlite:///sessions.db --host 0.0.0.0 --port 8888

curl --location 'http://localhost:5000/api/schedule/' \
--header 'Content-Type: application/json' \
--data '{
    "topic": "Team Meet",
    "start_time": "2025-11-15T14:30:00",
    "duration": 30,
    "timezone": "Asia/Kolkata",
    "join_before_host": true
  }'
```
