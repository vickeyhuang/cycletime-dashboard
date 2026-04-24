import requests
import os
import csv
import time
from datetime import datetime, timedelta
import urllib3
from jira_config import JIRA_USER, JIRA_TOKEN, JIRA_URL, JQL

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Custom field - storyPoints
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
STORY_POINTS_FIELD = "customfield_10035"

# Status groups – adjust to match your board/workflow
IN_PROGRESS_STATUSES = ['In Progress']   # add others if needed, e.g. 'In Review'
BLOCKED_STATUSES = ['Blocked']           # or [] if you do not use Blocked at all
DONE_STATUSES = ['Done']                 # add other done/closed statuses if needed

def get_all_issue_keys():
    url = f"{JIRA_URL}/rest/api/3/search/jql"
    all_issues = []
    seen_keys = set()
    max_results = 100
    next_page_token = None
    page = 1

    while True:
        payload = {
            "jql": JQL,
            "fields": ["summary", "assignee", "status", STORY_POINTS_FIELD, "project"],
            "maxResults": max_results
        }
        if next_page_token:
            payload["nextPageToken"] = next_page_token

        print(f"Fetching page {page} ...")
        response = requests.post(
            url,
            headers=HEADERS,
            json=payload,
            auth=(JIRA_USER, JIRA_TOKEN),
            verify=False
        )
        response.raise_for_status()
        data = response.json()
        issues = data.get('issues', [])
        if not issues:
            print("No more issues returned by API. Stopping.")
            break

        new_issues = 0
        for issue in issues:
            key = issue['key']
            if key not in seen_keys:
                seen_keys.add(key)
                fields = issue['fields']
                summary = fields['summary']
                assignee = fields['assignee']['displayName'] if fields['assignee'] else "Unassigned"
                status = fields['status']['name'] if fields['status'] else "Unknown"
                story_points = fields.get(STORY_POINTS_FIELD)
                story_points = story_points if story_points is not None else ''
                project = fields['project']['name'] if fields['project'] else "Unknown"
                all_issues.append({
                    'key': key,
                    'summary': summary,
                    'assignee': assignee,
                    'status': status,
                    'story_points': story_points,
                    'project': project
                })
                new_issues += 1

        print(f"Fetched {len(issues)} issues in this batch, {new_issues} new.")
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            print("No nextPageToken found. Pagination complete.")
            break

        page += 1
        time.sleep(1)

    print(f"Total unique issues fetched: {len(all_issues)}")
    return all_issues


def is_working_day(dt):
    """Return True if dt (datetime) is a working day (Mon–Fri)."""
    return dt.weekday() < 5  # 0=Mon, 6=Sun


def working_seconds_between(start, end):
    """
    Calculate working-time seconds between start and end, excluding weekends.
    Assumes:
      - Working days: Mon–Fri
      - Non-working days: Sat, Sun
      - For now, full 24h of a working day are counted as working.
    """
    if not start or not end or end <= start:
        return 0.0

    total = 0.0
    current = start

    # Iterate day by day
    while current < end:
        # Start of next calendar day
        next_day = (current + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = min(next_day, end)

        if is_working_day(current):
            total += (day_end - current).total_seconds()

        current = day_end

    return total


def hours_and_minutes_from_seconds(seconds):
    """
    Convert working-time seconds into (total_hours, minutes),
    truncating seconds (no rounding).
    """
    if seconds <= 0:
        return 0, 0
    total_minutes = int(seconds // 60)  # drop leftover seconds
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return hours, minutes


def format_wdhm_from_working_seconds(seconds):
    """
    Format working-time seconds into 'Xw Yd Zh Wm' with:
      - 1 working day  = 24 working hours
      - 1 working week = 7 working days = 168 working hours
      - minutes shown, seconds discarded
    """
    total_hours, minutes = hours_and_minutes_from_seconds(seconds)
    weeks = total_hours // (7 * 24)
    rem_hours = total_hours % (7 * 24)
    days = rem_hours // 24
    hours = rem_hours % 24

    parts = []
    if weeks:
        parts.append(f"{weeks}w")
    if days or weeks:  # show days if non-zero OR we already showed weeks
        parts.append(f"{days}d")
    parts.append(f"{hours}h")
    parts.append(f"{minutes}m")

    return " ".join(parts)


def business_days_and_weekends(start, end):
    """
    Calendar weekend logic:
    - Count calendar days between start.date() and end.date(), inclusive.
    - Classify each as weekday (business) or weekend.
    Used only to show how many weekend days the periods cross.
    """
    if start > end:
        start, end = end, start

    start_date = start.date()
    end_date = end.date()
    total_days = (end_date - start_date).days + 1

    business_days = 0
    weekend_days = 0

    for i in range(total_days):
        day = start_date + timedelta(days=i)
        if day.weekday() < 5:
            business_days += 1
        else:
            weekend_days += 1

    return business_days, weekend_days


def get_in_progress_and_block_periods(issue_key):
    """
    From the issue changelog, derive:
      - periods spent in IN_PROGRESS_STATUSES,
      - periods spent in BLOCKED_STATUSES,
      - first Done time.
    """
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}?expand=changelog"

    for attempt in range(5):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                auth=(JIRA_USER, JIRA_TOKEN),
                verify=False
            )
            response.raise_for_status()
            break
        except requests.exceptions.ConnectionError as e:
            print(f"Connection error for {issue_key}: {e}. Retrying in 5 seconds...")
            time.sleep(5)
    else:
        print(f"Failed to fetch changelog for {issue_key} after several retries.")
        return [], [], None

    data = response.json()
    changelog = data.get('changelog', {}).get('histories', [])
    changelog = sorted(changelog, key=lambda h: h['created'])

    in_progress_periods = []
    block_periods = []
    in_progress_start = None
    block_start = None
    done_time = None

    for history in changelog:
        for item in history.get('items', []):
            if item.get('field') != 'status':
                continue

            to_status = item.get('toString')
            from_status = item.get('fromString')
            transition_time = datetime.strptime(
                history['created'], "%Y-%m-%dT%H:%M:%S.%f%z"
            )

            # In Progress statuses
            if to_status in IN_PROGRESS_STATUSES:
                if in_progress_start is None:
                    in_progress_start = transition_time
            elif from_status in IN_PROGRESS_STATUSES and in_progress_start is not None:
                in_progress_periods.append((in_progress_start, transition_time))
                in_progress_start = None

            # Blocked statuses
            if to_status in BLOCKED_STATUSES:
                if block_start is None:
                    block_start = transition_time
            elif from_status in BLOCKED_STATUSES and block_start is not None:
                block_periods.append((block_start, transition_time))
                block_start = None

            # First time entering a Done status
            if to_status in DONE_STATUSES and done_time is None:
                done_time = transition_time

    # Close any open In Progress / Blocked periods at Done time, if any
    if done_time is not None:
        if in_progress_start is not None:
            in_progress_periods.append((in_progress_start, done_time))
        if block_start is not None:
            block_periods.append((block_start, done_time))

    return in_progress_periods, block_periods, done_time


def main():
    issues = get_all_issue_keys()
    output = []
    total = len(issues)
    print(f"Processing {total} issues for cycle time calculation...")

    batch_size = 20
    batch_keys = []

    for idx, issue in enumerate(issues, 1):
        batch_keys.append(issue['key'])
        in_progress_periods, block_periods, done_time = get_in_progress_and_block_periods(issue['key'])

        # Sum working-time seconds in each status group (exclude weekends)
        total_in_progress_working_seconds = sum(
            working_seconds_between(start, end) for start, end in in_progress_periods
        )
        total_block_working_seconds = sum(
            working_seconds_between(start, end) for start, end in block_periods
        )
        total_cycle_working_seconds = total_in_progress_working_seconds + total_block_working_seconds

        # Jira-like w d h m formatting based on working time
        in_progress_wdhm = format_wdhm_from_working_seconds(total_in_progress_working_seconds)
        blocked_wdhm = format_wdhm_from_working_seconds(total_block_working_seconds)
        cycle_wdhm = format_wdhm_from_working_seconds(total_cycle_working_seconds)

        # Converted Cycle Time in pure working days (24h working days) with 1 decimal place
        converted_cycle_time_days = round(
            total_cycle_working_seconds / (24 * 3600), 1
        ) if total_cycle_working_seconds > 0 else 0.0

        # Converted In Progress time in working days (24h working days) with 1 decimal place
        converted_in_progress_days = round(
            total_in_progress_working_seconds / (24 * 3600), 1
        ) if total_in_progress_working_seconds > 0 else 0.0

        # Start/end timestamps for reference (calendar)
        in_progress_date = ''
        done_date = ''
        done_in_month = ''

        if in_progress_periods:
            in_progress_date = in_progress_periods[0][0].strftime("%Y-%m-%dT%H:%M:%S%z")
            done_date = in_progress_periods[-1][1].strftime("%Y-%m-%dT%H:%M:%S%z")
        elif block_periods:
            in_progress_date = block_periods[0][0].strftime("%Y-%m-%dT%H:%M:%S%z")
            done_date = block_periods[-1][1].strftime("%Y-%m-%dT%H:%M:%S%z")

        if done_date:
            done_in_month = datetime.strptime(done_date, "%Y-%m-%dT%H:%M:%S%z").strftime("%Y-%m")

        # Cycle Time_0 from In Progress Date ~ Done Date (single span, working time, w d h m)
        cycle0_wdhm = ""
        if in_progress_date and done_date:
            ip_dt = datetime.strptime(in_progress_date, "%Y-%m-%dT%H:%M:%S%z")
            done_dt = datetime.strptime(done_date, "%Y-%m-%dT%H:%M:%S%z")
            cycle0_seconds = working_seconds_between(ip_dt, done_dt)
            cycle0_wdhm = format_wdhm_from_working_seconds(cycle0_seconds)

        # Weekend days using original calendar logic
        total_weekend_days = 0
        for period_start, period_end in in_progress_periods + block_periods:
            _, weekend_days = business_days_and_weekends(period_start, period_end)
            total_weekend_days += weekend_days

        highlight_weekend = "Yes" if total_weekend_days > 0 else "No"

        url = f"{JIRA_URL}/browse/{issue['key']}"

        output.append([
            issue['key'],
            issue['summary'],
            issue['project'],
            issue['assignee'],
            issue['status'],
            issue['story_points'],
            in_progress_date,
            done_date,
            cycle0_wdhm,                 # Cycle Time_0 (w d h m) – working time InProgDate~DoneDate
            cycle_wdhm,                  # Cycle Time (w d h m) – working InProg + Blocked
            converted_cycle_time_days,   # Converted Cycle Time (d) - (working days, 1 decimal)
            in_progress_wdhm,            # In Progress (w d h m)
            converted_in_progress_days,  # In Progress (d)  <-- NEW
            blocked_wdhm,                # Blocked (w d h m)
            total_weekend_days,
            highlight_weekend,
            done_in_month,
            url
        ])

        if idx % batch_size == 0 or idx == total:
            start_range = idx - batch_size + 1 if idx - batch_size + 1 > 0 else 1
            print(f"[{start_range}-{idx}] Processed issues: {', '.join(batch_keys)}")
            batch_keys = []

    # Create output directory if it doesn't exist
    output_dir = "output_files"
    os.makedirs(output_dir, exist_ok=True)        

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"output_jira_cycle_times_{timestamp}.csv"

    # Build full output path
    output_path = os.path.join(output_dir, output_filename)

    print(f"Writing results to {output_path} ...")
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'Issue Key', 'Summary', 'Project', 'Assignee', 'Status', 'Story Points',
            'In Progress Date', 'Done Date',
            'Cycle Time_0 (w d h m)',
            'Cycle Time (w d h m)',
            'Converted Cycle Time (d)',
            'In Progress (w d h m)', 'In Progress (d)',
            'Blocked (w d h m)',
            'Weekend Days', 'Highlight Weekend', 'Done in Month', 'URL'
        ])
        writer.writerows(output)

    print(f"Exported to {output_path}")


if __name__ == "__main__":
    main()
