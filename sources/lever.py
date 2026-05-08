import requests


def get_lever_jobs(board_token):
    """
    Fetch all public postings from a Lever job board.
    """
    url = f"https://api.lever.co/v0/postings/{board_token}?mode=json"
    print(f"Fetching jobs from: {url}")

    response = requests.get(url, timeout=30)

    if response.status_code == 404:
        print(f"That Lever slug was not found: {board_token}")
        return []

    response.raise_for_status()
    data = response.json()

    # Lever returns a list of postings directly when mode=json
    if isinstance(data, list):
        print(f"Found {len(data)} jobs")
        return data

    # Defensive: some responses wrap postings under a key
    if isinstance(data, dict) and "postings" in data:
        postings = data["postings"]
        print(f"Found {len(postings)} jobs")
        return postings

    print(f"Unexpected Lever response shape for {board_token}")
    return []


def get_lever_job_detail(board_token, source_job_id):
    """
    Lever's public posting list returns full description + comp inline,
    so the detail fetch is a no-op (matching Ashby's pattern).
    """
    return {}
