import requests


WORKABLE_BASE = "https://apply.workable.com"


def get_workable_jobs(board_token):
    """
    Fetch all public jobs from a Workable account via the widget API.
    Returns metadata only — descriptions and salary come from get_workable_job_detail.
    """
    url = f"{WORKABLE_BASE}/api/v1/widget/accounts/{board_token}"
    print(f"Fetching jobs from: {url}")

    response = requests.get(url, timeout=30)

    if response.status_code == 404:
        print(f"That Workable slug was not found: {board_token}")
        return []

    response.raise_for_status()
    data = response.json()
    jobs = data.get("jobs", []) or []

    print(f"Found {len(jobs)} jobs")
    return jobs


def get_workable_job_detail(board_token, source_job_id):
    """
    Workable's widget API returns metadata only. The /{slug}/jobs/view/{shortcode}.md
    endpoint returns the full job posting in clean markdown — title, salary, workplace,
    department, full description, requirements, benefits.
    """
    url = f"{WORKABLE_BASE}/{board_token}/jobs/view/{source_job_id}.md"
    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        return {}
    return {"markdown": response.text}
