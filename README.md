# DietNerd

![DietNerd Logo](dietnerd-website/assets/dietnerd_logo.png)

DietNerd (https://dietnerd.org/) is a web-based LLM-powered tool that answers diet and nutrition-related questions by extracting and summarizing information from academic papers sourced from PubMed. Users can ask questions on various topics, including dietary strategies, nutrition science, and health outcomes, and receive detailed, evidence-based responses based on the latest research. It utilizes a unique article search strategy and is built with multiple safety-motivated touchpoints, including a safety analysis that evaluates the pros, cons, and risks of the topics in question.

The tool is designed to provide reliable and up-to-date information for individuals, health professionals, and researchers alike. Our mission is to enrich conversations between patients and their medical providers and equip users with knowledge.

## Features

- Query-based diet and nutrition information
- Evidence-based answers backed by scientific research
- Similar question suggestions
- PDF generation of answers
- Reference analysis with links to full articles

## Technology Stack

- Frontend: HTML, CSS, JavaScript
- Backend API
## Project Structure

- `index.html`: Main page of the application
- `index.js`: Core functionality for querying and displaying answers
- `index.css`: Styles for the main page
- `reference.html`: Page for displaying detailed reference information
- `reference.js`: Functionality for the reference page
- `reference.css`: Styles for the reference page
- `about.html`: Information about DietNerd and the team
- `about.css`: Styles for the about page
- `contact.html`: Contact form for user feedback
- `contact.css`: Styles for the contact page
- `env.js`: Environment variables for API endpoints and email service

## Team

- Professor Dennis Shasha @ New York University
- Shela Wu @ New York University
- Zubair Yacub @ University of Illinois Urbana Champaign

## Disclaimer

DietNerd is an exploratory tool designed to enrich conversations with registered dietitians or registered dietitian nutritionists. The insights provided may not fully consider all potential medication interactions or pre-existing conditions. Always consult with a healthcare professional for personalized advice.


## Clean local verification

The API tests use MySQL-specific transactions, foreign keys, and locking, so they should not be replaced with SQLite. A disposable MySQL 8 fixture is provided for a clean-clone run:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
./tests/run_local_mysql_tests.sh
```

The script starts an isolated MySQL container, runs the API and concurrency integration tests plus the remaining test suite, and removes the database container and volume on exit. It uses port `33306` by default; set `CHRONICNERD_TEST_MYSQL_PORT` if that port is occupied.

For the browser fixture after the MySQL tests are green:

```bash
npm ci
# In separate terminals, with the same DB environment variables:
python tests/browser_test_server.py
python -m http.server 18080 --directory dietnerd-website
npm run test:browser
```

The browser fixture stubs only the external science APIs. A release claim still requires a separate live OpenAI/PubMed pass with valid credentials.
