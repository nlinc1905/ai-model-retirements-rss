# AI Model Retirements Scraper

AI models deprecate and retire like software packages, but unlike software packages, there is no standard way to warn developers of upcoming model retirements. This results in code based on specific models to break when the models are retired.

Hosts of AI models usually have pages dedicated to listing available models, and their expected retirement dates. So by scraping these pages, it is possible to have advance warning of changes that will break your code.

## What it does

Scrapes the following pages every day at midnight EST:
* https://platform.claude.com/docs/en/about-claude/model-deprecations
* https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html
* https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements

Publishes an RSS feed at https://nlinc1905.github.io/ai-model-retirements-rss/rss.xml

## How to Run

A cron job runs the service daily, but if you want to run it yourself, locally:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scrape.py
```

Outputs will be dumped to the `output` directory.

### Notes

- Each run compares the data to the previous run. For the first run, the output files are created. For subsequent runs, a CSV file with changes from the original will be produced, and the RSS feed will update.
