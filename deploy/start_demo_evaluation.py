"""Read-only review, authorized reset, or fresh-feed activation for DEMO only."""

import argparse
import json

from trade_agent.demo_evaluation import inspect, prepare, resume
from trade_agent.main import build_runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "prepare", "resume"])
    parser.add_argument("--approval-ref")
    args = parser.parse_args()
    service = build_runtime()[0].service
    if args.action == "prepare":
        if not args.approval_ref:
            parser.error("prepare requires --approval-ref from an explicit operator approval")
        result = prepare(service, args.approval_ref)
    elif args.action == "resume":
        result = resume(service)
    else:
        result = inspect(service)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
