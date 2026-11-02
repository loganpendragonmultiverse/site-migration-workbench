# Reproduce acceptance

Runtime validation uses Linux x86-64 containers with Python 3.11, 3.12 and 3.14. The host VPS has Python 3.9 and is not the supported runtime. No Windows/macOS or human accessibility result is inferred.

## Local tests and browser acceptance

```sh
python -m pip install -r requirements-dev.txt
ruff format --check smw tests
ruff check smw tests
python -m unittest discover -s tests -v
PYTHONPATH=. python tests/durability.py
PYTHONPATH=. python tests/benchmark.py
npm install --prefix .test-tools --no-audit --no-fund playwright@1.64.1 prettier@3.6.2 @axe-core/playwright@4.10.2
.test-tools/node_modules/.bin/prettier --check smw/app.js smw/index.html smw/style.css tests/*.mjs
```

Run browser scripts inside the matching Playwright v1.64.1 container, with Python 3.11+ available, the checkout as working directory and adequate shared memory:

```sh
node tests/ui.mjs
node tests/full_ui.mjs
node tests/scale_ui.mjs
node tests/accessibility.mjs
```

The full workflow accepts WORKBENCH_INSTALL_ROOT pointing at an isolated installed wheel directory. Its Python process uses isolated imports from that directory, ensuring that source checkout imports cannot mask missing packaged assets.

## Actual deployment servers

The exporter test requires Docker on its executor. It creates its own internal network and bounded containers, publishes no host ports, uses only synthetic data and removes its own containers/network. It never reads a real WordPress installation or production configuration.

```sh
mkdir -p .test-tools/downloads
curl --fail --location https://downloads.wordpress.org/plugin/redirection.5.10.1.zip --output .test-tools/downloads/redirection-5.10.1.zip
docker pull nginx:1.28-alpine
docker pull mariadb:11.8
docker pull wordpress:7.1.4-php8.3-apache
docker pull python:3.12-slim
python tests/export_servers.py
```

The downloaded Redirection 5.10.1 fixture SHA-256 is f68a4be447ecba00c64e8a39d7517807ed49cc18e77b7a364c20cb31848235b7. Do not treat a version label alone as proof that downloaded bytes match.

Current observed image digests:
- Nginx: a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
- WordPress 7.1.4: 12ec50e21cb81a5ea964669d434578b7da4a101a45aef83f5f1c2227bcd1b058
- MariaDB: c4124d1afa875e47f2892edf26b482fa54f796e2f342d3717a04ca091a22a9bd
- Python 3.12: a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

Use tag@sha256:digest when strict fixture reproduction is required. Public WordPress currently identifies [7.1.4 as its security release](https://wordpress.org/news/2026/10/wordpress-7-1-4/); the [Redirection changelog](https://en-gb.wordpress.org/plugins/redirection/) documents the newer import API. Earlier compatibility evidence remains separate and is not a recommendation to deploy an old security patch level.

The script can exercise a separately downloaded legacy plugin using SMW_WORDPRESS_IMAGE and SMW_REDIRECTION_VERSION. Native WordPress export/delete/reimport must yield identical HTTP observations. Test configuration snippets or imported counts alone are insufficient.

## Existing capture + CSV comparison

The original 1,200-source corpus is executable with LinkWitness v1.2.1 available on PYTHONPATH. Use its exact released source alongside this checkout; it is a test-only baseline, not an application runtime dependency. Run `PYTHONPATH=.:/path/to/linkwitness/src python tests/comparison.py`. The preserved inspected source commit was cb370f4a1969fe39d1b6164e70d3f94e66637032.

The script writes the expected CSV, two capture batches per run, joined CSV results and regression evidence. Workbench uses the same original truth, one frozen revision and two durable runs. It checks all 1,200 result states and the 800 exported redirect/retirement decisions. Workbench delays are disabled only for this loopback fixture; the recorded durations are not default production timings or human-time savings. See docs/COMPARISON.md.
