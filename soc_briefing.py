"""
SOC Morning Briefing — Automated Script (Teams size-limit fix)
---------------------------------------------------------------
Splits the briefing into one Teams message per section to stay
under the 28 KB Teams webhook limit.

Requirements:
    pip install anthropic requests

Setup:
    1. Set your CLAUDE_API_KEY below (from platform.anthropic.com)
    2. Set your TEAMS_WEBHOOK_URL below (from Teams channel > Connectors > Incoming Webhook)
    3. Schedule with Windows Task Scheduler or cron (see bottom of file)
"""

import anthropic
import requests
import json
import time
import re
from datetime import date

# ─────────────────────────────────────────────
# CONFIGURATION — Edit these before running
# ─────────────────────────────────────────────

CLAUDE_API_KEY = "INSERT CLAUDE API KEY"
TEAMS_WEBHOOK_URL = "TEAMS WEBHOOK URL"

# Optional: customise for your environment
INDUSTRY = "MSSP"
REGION = "EUROPE"

# ─────────────────────────────────────────────
# BRIEFING PROMPT
# ─────────────────────────────────────────────
 
def build_prompt() -> str:
    """Build the briefing prompt. On Mondays, covers Friday-Sunday."""
    weekday = date.today().weekday()  # 0=Monday, 6=Sunday
 
    if weekday == 0:
        time_instruction = (
            "This is the Monday morning briefing. Cover cybersecurity news and incidents "
            "from the past three days — Friday, Saturday and Sunday. Include anything "
            "notable that happened over the weekend."
        )
        briefing_label = "Weekend + Monday"
    else:
        time_instruction = "Cover cybersecurity news and incidents from the last 24 hours."
        briefing_label = "Daily"
 
    return f"""You are a cybersecurity intelligence assistant for a SOC team.
I am a Principal Expert with 20 years of experience and SOC Lead.
 
Search the web for the latest cybersecurity news and produce a structured {briefing_label} morning briefing for our SOC analysts.
{time_instruction}
 
Use EXACTLY these five section headers (keep the numbering and emoji):
1. 🔴 Critical Vulnerabilities & Patches
2. 🟠 Active Threats & Campaigns
3. 📋 Data Breaches & Incidents
4. 🔍 Threat Intel Highlights
5. ✅ SOC Analysts Takeaway
 
Focus on threats relevant to {INDUSTRY} and {REGION}.
Be concise. Skip vendor marketing. Flag anything requiring immediate attention with 🚨.
Format output in Markdown. Provide references where applicable. Make section 5 a bullet list."""
 
 
def get_briefing() -> str:
    """Call Claude API with web search to generate the briefing.
    Retries up to 4 times with exponential backoff on rate limit errors."""
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
 
    max_retries = 4
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 5
                    }
                ],
                messages=[
                    {"role": "user", "content": build_prompt()}
                ]
            )
 
            briefing_text = ""
            for block in response.content:
                if block.type == "text":
                    briefing_text += block.text
 
            return briefing_text.strip()
 
        except Exception as e:
            is_rate_limit = "429" in str(e) or "rate_limit" in str(e)
            if is_rate_limit and attempt < max_retries - 1:
                wait = 60 * (attempt + 1)   # 60s, 120s, 180s
                print(f"  Rate limit hit - waiting {wait}s before retry {attempt + 2}/{max_retries}...")
                time.sleep(wait)
            else:
                raise
 
 
def split_into_sections(briefing: str) -> list:
    """
    Split the briefing into sections by detecting any numbered markdown heading.
    Flexible matching so minor wording changes from Claude do not break it.
    Returns a list of dicts with 'title' and 'body' keys.
    """
    # Matches any line like: "## 1. 🔴 Title" or "1. Title" or "### 2. Something"
    title_pattern = re.compile(r'^(#{0,3}\s*\d+\.\s+.+)$', re.MULTILINE)
 
    matches = list(title_pattern.finditer(briefing))
    print(f"  [parser] Found {len(matches)} section headers:")
    for m in matches:
        print(f"  [parser]   -> '{m.group(0).strip()}'")
 
    if not matches:
        print("  [parser] WARNING: No headers matched - sending as single block")
        return [{"title": "SOC Briefing", "body": briefing}]
 
    sections = []
    for i, match in enumerate(matches):
        title = match.group(0).strip().lstrip("#").strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(briefing)
        body = briefing[start:end].strip()
        body = re.sub(r'^[-]{3,}\s*\n?', '', body).strip()
        if body:
            sections.append({"title": title, "body": body})
 
    return sections
 
 
def build_teams_payload(title: str, body: str, is_header: bool = False) -> dict:
    """Build a Teams Adaptive Card payload for a single section."""
 
    blocks = []
 
    if is_header:
        blocks.append({
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
            "wrap": True
        })
        # Teams rejects Adaptive Cards with only one block and no body text,
        # so always add a subtitle line to the header card
        blocks.append({
            "type": "TextBlock",
            "text": "Daily threat intelligence briefing for the SOC team.",
            "isSubtle": True,
            "wrap": True
        })
    else:
        blocks.append({
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium",
            "color": "Warning",
            "wrap": True
        })
 
    if body:
        blocks.append({
            "type": "TextBlock",
            "text": body,
            "wrap": True,
            "spacing": "Small"
        })
 
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": blocks
                }
            }
        ]
    }
 
 
def post_to_teams(payload: dict) -> bool:
    """Post a single payload to the Teams webhook."""
    response = requests.post(
        TEAMS_WEBHOOK_URL,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=30
    )
    if response.status_code not in (200, 202):
        print(f"    → HTTP {response.status_code}: {response.text[:200]}")
    return response.status_code in (200, 202)
 
 
def main():
    today_dt = date.today()
    today = today_dt.strftime("%B %d, %Y")
    is_monday = today_dt.weekday() == 0
    briefing_label = "Weekend + Monday" if is_monday else "Daily"
    print(f"Generating SOC {briefing_label} briefing for {today}...")
 
    try:
        briefing = get_briefing()
        print("Briefing generated.")
        print(f"  [debug] Briefing length: {len(briefing)} chars")
        print("  [debug] Last 200 chars:", briefing[-200:])
    except Exception as e:
        print(f"Failed to generate briefing: {e}")
        return
 
    # Split into sections
    sections = split_into_sections(briefing)
    print(f"Sending {len(sections) + 1} messages to Teams...")
 
    # Post header message first
    header_payload = build_teams_payload(
        title=f"SOC {briefing_label} Briefing — {today}",
        body="",
        is_header=True
    )
    if post_to_teams(header_payload):
        print("Header posted")
    else:
        print("Failed to post header")
 
    # Delay after header to ensure it appears first in Teams
    time.sleep(2)
 
    # Sort sections by their leading number to guarantee correct order
    def section_order(s):
        m = re.match(r'\d+', s["title"].lstrip("#").strip())
        return int(m.group()) if m else 99
    sections = sorted(sections, key=section_order)
 
    # Post each section as a separate message
    for i, section in enumerate(sections):
        payload = build_teams_payload(
            title=section["title"],
            body=section["body"],
            is_header=False
        )
 
        # Check payload size before sending (28KB limit)
        payload_size = len(json.dumps(payload).encode("utf-8"))
        if payload_size > 27000:
            print(f"Section '{section['title']}' is {payload_size} bytes — truncating...")
            section["body"] = section["body"][:1500] + "\n\n_[truncated — content too large]_"
            payload = build_teams_payload(section["title"], section["body"])
 
        if post_to_teams(payload):
            print(f"Section {i+1}/{len(sections)}: '{section['title']}' posted")
        else:
            print(f"Failed to post section: '{section['title']}'")
 
        time.sleep(2)  # Increased delay to ensure Teams delivers messages in order
 
    print("Done! Briefing posted to Teams.")
 
 
if __name__ == "__main__":
    main()
 
 
# ─────────────────────────────────────────────
# SCHEDULING INSTRUCTIONS
# ─────────────────────────────────────────────
#
# WINDOWS — Task Scheduler:
#   1. Open Task Scheduler > Create Basic Task
#   2. Trigger: Daily at 09:00
#   3. Action: Start a program
#      Program: python
#      Arguments: C:\path\to\soc_briefing.py
#
# LINUX / MAC — Cron:
#   Run: crontab -e
#   Add: 0 9 * * 1-5 /usr/bin/python3 /path/to/soc_briefing.py
#   (runs Mon-Fri at 9AM)
#
# TEAMS WEBHOOK SETUP:
#   1. Go to your Teams channel
#   2. Click "..." > Connectors > Incoming Webhook > Configure
#   3. Name it "SOC Briefing", copy the webhook URL
#   4. Paste it into TEAMS_WEBHOOK_URL above
#
# CLAUDE API KEY:
#   1. Go to platform.anthropic.com
#   2. API Keys > Create Key
#   3. Paste it into CLAUDE_API_KEY above
# ─────────────────────────────────────────────
