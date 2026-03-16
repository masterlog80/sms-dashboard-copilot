# sms-dashboard-copilot

Instructions:
1. Copy all the file on the same folder
```
git clone https://github.com/masterlog80/sms-dashboard-copilot.git
cd sms-dashboard-copilot
```
2. Build the docker image:
```
yes | docker image prune --all
docker build -t sms-dashboard-copilot .
```
3. Deploy the composer file:
```
docker compose -f docker-compose.yml up -d --remove-orphans
```
