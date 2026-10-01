# Targets marked 💲 create or scale billable GCP resources. See README "Cost" section.
SHELL := /bin/bash
.DEFAULT_GOAL := help

-include .env
export

TF_DIR     := infra/terraform/envs/dev
PYTHON     := .venv/bin/python
TARGET     ?= vllm-gke

.PHONY: help setup lint test tf-plan up deploy-vllm deploy-tgi deploy-vertex bench finetune down

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

bench: ## Run load generator against TARGET (vllm-gke | tgi-gke | vllm-vertex | mock)
	@echo "TODO: implemented in benchmark phase (TARGET=$(TARGET))"; exit 1

finetune: ## 💲 LoRA fine-tune on a spot L4
	@echo "TODO: implemented in fine-tuning phase"; exit 1

down: ## Tear down: undeploy Vertex endpoints, delete workloads, terraform destroy
	@echo "TODO: implemented in infra phase"; exit 1
