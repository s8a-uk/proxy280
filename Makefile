COMPOSE := docker compose
# ホストの UID で実行し、マウント先に root 所有のファイルを作らない
# (権限とネットワークは compose.yaml の dev で絞っている)
DEV := $(COMPOSE) run --rm --user $$(id -u):$$(id -g) dev

.PHONY: up down logs build lock test lint fmt check github-settings

up: allowlist.txt
	$(COMPOSE) up -d --build proxy280

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f proxy280

build:
	$(COMPOSE) build

# 許可リストが無ければサンプルから作る (既存のファイルは上書きしない)
allowlist.txt:
	cp allowlist.example.txt $@

# uv.lock を更新する (dev イメージは uv.lock に依存するため、uv だけの base ステージで実行)
lock:
	docker build -q --target base -t proxy280:base . > /dev/null
	docker run --rm -u $$(id -u):$$(id -g) --cap-drop=ALL --security-opt no-new-privileges \
		-e UV_CACHE_DIR=/tmp/uv -v $(CURDIR):/app -w /app proxy280:base uv lock $(LOCK_ARGS)

test:
	$(DEV) pytest

lint:
	$(DEV) ruff check .
	$(DEV) ruff format --check .

fmt:
	$(DEV) ruff check --fix .
	$(DEV) ruff format .

check: lint test

# GitHub リポジトリの設定 (アクションの制限・Dependabot・ルールセット等) を gh で適用する
github-settings:
	bash .github/apply-repository-settings.sh

# 環境固有のターゲット (デプロイ等) は local.mk に書く (git 管理外)
-include local.mk
