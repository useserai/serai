from sources.greenhouse import get_greenhouse_jobs, get_greenhouse_job_detail
from sources.ashby import get_ashby_jobs, get_ashby_job_detail
from sources.lever import get_lever_jobs, get_lever_job_detail
from sources.workable import get_workable_jobs, get_workable_job_detail
from sources.smartrecruiters import get_smartrecruiters_jobs, get_smartrecruiters_job_detail
from sources.workday import get_workday_jobs, get_workday_job_detail


def fetch_jobs_for_company(company_config):
    source = company_config["source"]

    if source == "greenhouse":
        return get_greenhouse_jobs(company_config["board_token"])

    if source == "ashby":
        return get_ashby_jobs(company_config["board_token"])

    if source == "lever":
        return get_lever_jobs(company_config["board_token"])

    if source == "workable":
        return get_workable_jobs(company_config["board_token"])

    if source == "smartrecruiters":
        return get_smartrecruiters_jobs(company_config["board_token"])

    if source == "workday":
        return get_workday_jobs(company_config)

    raise ValueError(f"Unsupported source: {source}")


def fetch_job_detail_for_company(company_config, source_job_id):
    source = company_config["source"]

    if source == "greenhouse":
        return get_greenhouse_job_detail(company_config["board_token"], source_job_id)

    if source == "ashby":
        return get_ashby_job_detail(company_config["board_token"], source_job_id)

    if source == "lever":
        return get_lever_job_detail(company_config["board_token"], source_job_id)

    if source == "workable":
        return get_workable_job_detail(company_config["board_token"], source_job_id)

    if source == "smartrecruiters":
        return get_smartrecruiters_job_detail(company_config["board_token"], source_job_id)

    if source == "workday":
        return get_workday_job_detail(company_config, source_job_id)

    return {}
