# DRAUPNIR. Every target delegates to tasks.py, so `make`, `make.ps1` and
# the pipeline run exactly the same commands.
#
#   make dev   -- clean machine to a running stack with seeded data (AC-Q9)
#   make ci    -- every pipeline stage of SAD 11H, in order

PYTHON ?= python3

.DEFAULT_GOAL := help
.PHONY: help bootstrap hooks format lint typecheck lint-web imports secrets audit sbom crypto-inventory openapi clients clients-check openapi-diff migrate-dry migrate reset-db seed test-unit test-property test-contract test-integration test-frontend test-e2e test-a11y test-visual test images build-web up down logs api web dev smoke static ci clean

help:
	@$(PYTHON) tasks.py --list

bootstrap:
	@$(PYTHON) tasks.py bootstrap

hooks:
	@$(PYTHON) tasks.py hooks

format:
	@$(PYTHON) tasks.py format

lint:
	@$(PYTHON) tasks.py lint

typecheck:
	@$(PYTHON) tasks.py typecheck

lint-web:
	@$(PYTHON) tasks.py lint-web

imports:
	@$(PYTHON) tasks.py imports

secrets:
	@$(PYTHON) tasks.py secrets

audit:
	@$(PYTHON) tasks.py audit

sbom:
	@$(PYTHON) tasks.py sbom

crypto-inventory:
	@$(PYTHON) tasks.py crypto-inventory

openapi:
	@$(PYTHON) tasks.py openapi

clients:
	@$(PYTHON) tasks.py clients

clients-check:
	@$(PYTHON) tasks.py clients-check

openapi-diff:
	@$(PYTHON) tasks.py openapi-diff

migrate-dry:
	@$(PYTHON) tasks.py migrate-dry

migrate:
	@$(PYTHON) tasks.py migrate

reset-db:
	@$(PYTHON) tasks.py reset-db

seed:
	@$(PYTHON) tasks.py seed

test-unit:
	@$(PYTHON) tasks.py test-unit

test-property:
	@$(PYTHON) tasks.py test-property

test-contract:
	@$(PYTHON) tasks.py test-contract

test-integration:
	@$(PYTHON) tasks.py test-integration

test-frontend:
	@$(PYTHON) tasks.py test-frontend

test-e2e:
	@$(PYTHON) tasks.py test-e2e

test-a11y:
	@$(PYTHON) tasks.py test-a11y

test-visual:
	@$(PYTHON) tasks.py test-visual

test:
	@$(PYTHON) tasks.py test

images:
	@$(PYTHON) tasks.py images

build-web:
	@$(PYTHON) tasks.py build-web

up:
	@$(PYTHON) tasks.py up

down:
	@$(PYTHON) tasks.py down

logs:
	@$(PYTHON) tasks.py logs

api:
	@$(PYTHON) tasks.py api

web:
	@$(PYTHON) tasks.py web

dev:
	@$(PYTHON) tasks.py dev

smoke:
	@$(PYTHON) tasks.py smoke

static:
	@$(PYTHON) tasks.py static

ci:
	@$(PYTHON) tasks.py ci

clean:
	@$(PYTHON) tasks.py clean
