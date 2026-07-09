from dotenv import load_dotenv
load_dotenv()
import os, sys
sys.path.insert(0,"src")
from cicd_orchestrator.github_client import GitHubClient
gh = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_OWNER"], "CICD")
pushed, skipped = gh.push_directory(".", branch="main", commit_message="Package mcp_server as installable console script for uvx/git usage [CI/CD Orchestrator Agent]")
print("Files pushed:", pushed)
print("Files skipped:", skipped)
