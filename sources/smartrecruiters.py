import requests


SR_BASE = "https://api.smartrecruiters.com/v1"
SR_LIMIT = 100  # max postings per request; all known endpoints respect this


def get_smartrecruiters_jobs(board_token):
    """
    Fetch all public postings from a SmartRecruiters company.
    SmartRecruiters paginates via offset; we walk pages until totalFound is exhausted.
    """
    all_postings = []
    offset = 0

    while True:
        url = f"{SR_BASE}/companies/{board_token}/postings?limit={SR_LIMIT}&offset={offset}"
        if offset == 0:
            print(f"Fetching jobs from: {url}")

        response = requests.get(url, timeout=30)

        if response.status_code == 404:
            print(f"That SmartRecruiters company was not found: {board_token}")
            return []

        response.raise_for_status()
        data = response.json()
        postings = data.get("content", []) or []
        all_postings.extend(postings)

        total = data.get("totalFound", 0)
        if not postings or len(all_postings) >= total or len(postings) < SR_LIMIT:
            break

        offset += SR_LIMIT

    print(f"Found {len(all_postings)} jobs")
    return all_postings


def get_smartrecruiters_job_detail(board_token, source_job_id):
    """
    Fetch full posting detail. List response is metadata-only; this call returns
    jobAd.sections with companyDescription / jobDescription / qualifications /
    additionalInformation, plus full URLs.
    """
    url = f"{SR_BASE}/companies/{board_token}/postings/{source_job_id}"
    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        return {}
    return response.json()
