#!/usr/bin/env python3
"""
Simple portfolio display script.

Run: python Untitled-1.py
"""

import json
import textwrap


def load_portfolio():
    # Example portfolio data. Edit or extend as needed.
    return {
        "name": "Your Name",
        "title": "Software Developer",
        "summary": "Passionate developer with experience in Python, Web, and Data.",
        "skills": ["Python", "Django", "Flask", "JavaScript", "SQL"],
        "projects": [
            {
                "name": "Project Alpha",
                "description": "A web app that does X.",
                "link": "https://example.com/alpha"
            },
            {
                "name": "Project Beta",
                "description": "A data pipeline for Y.",
                "link": "https://example.com/beta"
            }
        ],
        "contact": {
            "email": "you@example.com",
            "github": "https://github.com/yourusername",
        }
    }


def display_portfolio(p):
    sep = "=" * 60
    print(sep)
    print(f"{p['name']} - {p['title']}")
    print(sep)
    print(textwrap.fill(p['summary'], width=60))
    print()
    print("Skills:")
    print(', '.join(p['skills']))
    print()
    print("Projects:")
    for proj in p['projects']:
        print(f" - {proj['name']}: {proj['description']}")
        if proj.get('link'):
            print(f"   Link: {proj['link']}")
    print()
    print("Contact:")
    for k, v in p['contact'].items():
        print(f" {k.capitalize()}: {v}")
    print(sep)


def save_portfolio(p, filename="portfolio.json"):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=2)


def main():
    p = load_portfolio()
    display_portfolio(p)
    # Uncomment to save to file:
    # save_portfolio(p)


if __name__ == '__main__':
    main()
