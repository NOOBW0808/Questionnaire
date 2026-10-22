"""Open the latest Allure report without requiring PowerShell script execution."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allure",default=shutil.which("allure") or shutil.which("allure.bat"))
    args=parser.parse_args()
    if not args.allure:
        parser.error("Allure CLI not found; specify --allure with its absolute path")
    latest=Path(__file__).resolve().parent/"latest-run.json"
    if not latest.is_file():
        parser.error("Run test/run_api_tests.py first")
    run=json.loads(latest.read_text("utf-8"))
    report=Path(run["reportDirectory"])
    if not (report/"index.html").is_file():
        parser.error("Latest report was not generated successfully")
    print("Opening Allure report:",report,flush=True)
    return subprocess.call([args.allure,"open",str(report)])


if __name__=="__main__":
    raise SystemExit(main())
