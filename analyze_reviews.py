#!/usr/bin/env python3
"""Entry point: python analyze_reviews.py --app-id ... --store ... [--count N | --from D --to D]"""
import sys

from review_analyzer.cli import main

if __name__ == "__main__":
    sys.exit(main())
