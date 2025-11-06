# UiTM Forum Scraper - Every 10 minutes from 6 AM to 12 AM (midnight)
# Format: minute hour day month weekday command

# Run every 10 minutes during 6 AM to 11:59 PM (6am-midnight)
# Hours: 6-23 (6 AM to 11 PM)
# Minutes: 0,10,20,30,40,50 (every 10 minutes)

0,10,20,30,40,50 6-23 * * * cd /app && python ufuture-scraper.py >> /app/logs/cron.log 2>&1
