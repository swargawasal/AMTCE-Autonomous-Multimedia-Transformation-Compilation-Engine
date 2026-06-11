import requests
import json

def check_runs():
    url = "https://api.github.com/repos/swargawasal/AMTCE-Autonomous-Multimedia-Transformation-Compilation-Engine/actions/runs"
    headers = {
        "Accept": "application/vnd.github+json"
    }
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print(f"Error fetching runs: {r.status_code}")
        print(r.text)
        return
    
    data = r.json()
    runs = data.get("workflow_runs", [])
    print(f"Found {len(runs)} runs:")
    for run in runs[:5]:
        print(f"Run #{run['run_number']}: {run['head_commit']['message'][:50]} | Status: {run['status']} | Conclusion: {run['conclusion']}")
        if run['conclusion'] == 'failure':
            # Try to get jobs/logs URL
            jobs_url = run['jobs_url']
            rj = requests.get(jobs_url, headers=headers)
            if rj.status_code == 200:
                jobs = rj.json().get("jobs", [])
                for job in jobs:
                    print(f"  Job: {job['name']} | Conclusion: {job['conclusion']}")
                    # If failed, print steps
                    for step in job.get("steps", []):
                        if step['conclusion'] == 'failure':
                            print(f"    Failed Step: {step['name']} | Status: {step['conclusion']}")

if __name__ == "__main__":
    check_runs()
