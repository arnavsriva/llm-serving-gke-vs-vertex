# Targets marked 💲 create or scale billable GCP resources. See README "Cost" section.
SHELL := /bin/bash
.DEFAULT_GOAL := help

-include .env
export

TF_DIR := infra/terraform/envs/dev
PYTHON := .venv/bin/python

# Benchmark settings. .env or the command line override them, e.g.
#   make bench BENCH_TARGET=sglang-gke BENCH_BASE_URL=http://localhost:8001 CONCURRENCY=1,4,16
BENCH_BASE_URL ?= http://localhost:8000
BENCH_TARGET   ?= mock
MODEL_ID       ?= mock-model
BENCH_API      ?= completions
CONCURRENCY    ?= 1,2,4,8,16,32,64
REQUESTS       ?= 100
PROMPT_LEN     ?= uniform:200,800
OUTPUT_LEN     ?= uniform:128,384
BENCH_ARGS     ?=
RUNS           ?=

.PHONY: help setup lint test lock mock bench-smoke bench report validate-manifests kind-up kind-e2e \
	kind-down tf-plan up deploy-vllm deploy-sglang deploy-vertex finetune down

help: ## Show targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

setup: ## Create Python 3.11 venv, install dev deps, install pre-commit hooks
	uv venv --python 3.11 .venv
	uv pip install --python $(PYTHON) -e ".[dev]"
	.venv/bin/pre-commit install

lint: ## Ruff, yamllint, terraform fmt check, kubeconform on every rendered k8s env
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/yamllint -s .
	terraform fmt -check -recursive infra/terraform
	scripts/validate_manifests.sh

test: ## Unit tests (no cloud access)
	.venv/bin/pytest

lock: ## Re-pin the bench image's runtime dependencies (bench/requirements.txt)
	{ printf '# Pinned runtime dependencies for the bench container image (load generator + mock server).\n# Regenerate with: make lock\n\n'; \
	  uv pip compile pyproject.toml --python-version 3.11 --python-platform linux --generate-hashes --no-header -q; \
	} > bench/requirements.txt

mock: ## Run the CPU-only mock OpenAI-compatible server on :8000 (no cloud)
	$(PYTHON) -m bench mock --port 8000

bench-smoke: ## Validate the load generator against the mock -> results/validation/ (no cloud)
	$(PYTHON) -m bench smoke

validate-manifests: ## Render every k8s env and validate it with kubeconform (no cluster)
	scripts/validate_manifests.sh

kind-up: ## Local kind cluster shaped like GKE (CPU node + tainted fake-GPU node), image loaded
	scripts/kind.sh up

kind-e2e: ## On kind with mock servers: scheduling, readiness, PDB, in-cluster load test, dry runs
	scripts/kind_e2e.sh

kind-down: ## Delete the local kind cluster
	scripts/kind.sh down

tf-plan: ## Terraform plan for dev (no changes applied)
	terraform -chdir=$(TF_DIR) init -backend-config="bucket=$(TF_STATE_BUCKET)"
	terraform -chdir=$(TF_DIR) plan -out=tfplan

up: ## 💲 Apply Terraform (cluster, GPU pool at 0 nodes, Artifact Registry, IAM)
	@echo "TODO: implemented in infra phase"; exit 1

deploy-vllm: ## 💲 Deploy vLLM to GKE (scales GPU pool up)
	@echo "TODO: implemented in GKE serving phase"; exit 1

deploy-sglang: ## 💲 Deploy SGLang to GKE (scales GPU pool up)
	@echo "TODO: implemented in GKE serving phase"; exit 1

deploy-vertex: ## 💲 Build/push container, upload to Model Registry, deploy endpoint
	@echo "TODO: implemented in Vertex phase"; exit 1

bench: ## Concurrency sweep against BENCH_BASE_URL -> results/<timestamp>_<BENCH_TARGET>/
	$(PYTHON) -m bench run --base-url "$(BENCH_BASE_URL)" --target "$(BENCH_TARGET)" \
		--model "$(MODEL_ID)" --api "$(BENCH_API)" --concurrency "$(CONCURRENCY)" \
		--requests-per-level "$(REQUESTS)" --prompt-len "$(PROMPT_LEN)" \
		--output-len "$(OUTPUT_LEN)" $(BENCH_ARGS)

report: ## Plots + cost table for RUNS="results/<run-a> results/<run-b>" -> results/report/
	@test -n "$(RUNS)" || { echo 'usage: make report RUNS="results/<run-a> results/<run-b>"'; exit 1; }
	$(PYTHON) -m bench plot $(RUNS) --out results/report
	$(PYTHON) -m bench cost $(RUNS) --prices bench/prices.toml --out results/report

finetune: ## 💲 LoRA fine-tune on a spot L4
	@echo "TODO: implemented in fine-tuning phase"; exit 1

down: ## Tear down: undeploy Vertex endpoints, delete workloads, terraform destroy
	@echo "TODO: implemented in infra phase"; exit 1
