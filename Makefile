# Targets marked 💲 create or scale billable GCP resources. See README "Cost" section.
SHELL := /bin/bash
.DEFAULT_GOAL := help

-include .env
export

TF_DIR := infra/terraform/envs/dev
PYTHON := .venv/bin/python

# Benchmark settings. .env or the command line override them, e.g.
#   make bench BENCH_TARGET=tgi-gke BENCH_BASE_URL=http://localhost:8080 CONCURRENCY=1,4,16
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

.PHONY: help setup lint test mock bench-smoke bench report tf-plan up deploy-vllm deploy-tgi \
	deploy-vertex finetune down

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

setup: ## Create Python 3.11 venv, install dev deps, install pre-commit hooks
	uv venv --python 3.11 .venv
	uv pip install --python $(PYTHON) -e ".[dev]"
	.venv/bin/pre-commit install

lint: ## Ruff, yamllint, terraform fmt check
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/yamllint -s .
	terraform fmt -check -recursive infra/terraform

test: ## Unit tests (no cloud access)
	.venv/bin/pytest

mock: ## Run the CPU-only mock OpenAI-compatible server on :8000 (no cloud)
	$(PYTHON) -m bench mock --port 8000

bench-smoke: ## Validate the load generator against the mock -> results/validation/ (no cloud)
	$(PYTHON) -m bench smoke

tf-plan: ## Terraform plan for dev (no changes applied)
	terraform -chdir=$(TF_DIR) init -backend-config="bucket=$(TF_STATE_BUCKET)"
	terraform -chdir=$(TF_DIR) plan -out=tfplan

up: ## 💲 Apply Terraform (cluster, GPU pool at 0 nodes, Artifact Registry, IAM)
	@echo "TODO: implemented in infra phase"; exit 1

deploy-vllm: ## 💲 Deploy vLLM to GKE (scales GPU pool up)
	@echo "TODO: implemented in GKE serving phase"; exit 1

deploy-tgi: ## 💲 Deploy TGI to GKE (scales GPU pool up)
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
