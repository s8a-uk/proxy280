# proxy280

[280blocker](https://280blocker.net) のドメインリストを、固定 URL で配信する LAN 内向けプロキシ。

280blocker のリストは月ごとにファイル名が変わる（`280blocker_domain_ag_YYYYMM.txt`）ため、DNS フィルタに直接登録すると毎月 URL を書き換える必要がある。proxy280 は当月分を取得して固定 URL で返す。

> [!NOTE]
> 280blocker（トビラシステムズ株式会社）とは無関係の非公式ツール。

## 依存ライブラリ

実行時の依存ライブラリはない。Python 3.15 の標準ライブラリだけで動く（`pyproject.toml` の `dependencies = []`）。

- 本番イメージ（Dockerfile の `runtime` ステージ）には proxy280 本体だけが入る
- pytest・ruff は開発用の依存グループ（`[dependency-groups] dev`）にあり、dev コンテナと CI でだけ使う

依存を増やすときも、実行時の依存はできるだけ足さない。テストや lint のためのものは `dev` グループに足す。

## エンドポイント

| パス | 内容 |
|---|---|
| `GET /blocklist` | 当月の 280blocker リスト。`X-Blocklist-Month` ヘッダに対象月（例: `2026-10`） |
| `GET /allowlist` | `allowlist.txt` の内容（`@@\|\|domain^` 形式） |
| `GET /healthz` | ヘルスチェック（`ok` を返す） |

`/blocklist` の挙動:

- 上流は月ごとの URL のまま中身を随時更新するため、取得したリストはメモリに保持し、`PROXY280_REFRESH_INTERVAL` 秒（既定 24 時間）ごとに上流の更新を確認する
- 更新の確認は ETag による条件付きリクエスト（`If-None-Match`）で行い、未更新なら本体を取り直さない（304）
- 月が変わったら、確認間隔を待たずに当月分を取得する（月は `PROXY280_TIMEZONE` 基準）
- 取得に失敗した場合は保持中のリストを返し続け、`PROXY280_RETRY_INTERVAL` 秒後に再試行する
- 起動直後に当月分が取得できない場合（月初の公開前など）は前月分を返し、`PROXY280_RETRY_INTERVAL` 秒後に当月分を再試行する
- 保持はメモリのみ。再起動後は最初のリクエスト時に取得し直す
- どのリストも取得できない場合は 503

## 使い方

```sh
make up    # allowlist.txt が無ければ allowlist.example.txt から作って起動
```

`http://<ホスト>:28000/blocklist` で当月のリストが返る。

## 280blocker のファイルの利用条件

[280blocker のダウンロードページ](https://280blocker.net/download/)には、配布ファイルについて次のように記載されている。

> 280blocker.net内で配布しているファイルについて。
> 著作権法で許容される個人用途に限ります。
> 営利目的での使用・再配布などは禁止です。

proxy280 は、自宅 LAN 内の DNS フィルタが個人用途でリストを取得するためのものである。

- proxy280 をインターネットや第三者に公開しないこと（リストの再配布にあたる）
- 営利目的で使わないこと
- 上流への取得は、更新確認（既定 24 時間ごと、未更新なら本体は転送されない）と失敗時の再試行に限っている。間隔を短くしすぎないこと

## DNS フィルタへの登録

DNS フィルタの設定に以下の URL を追加する。

| 種類 | URL |
|---|---|
| DNS ブロックリスト | `http://<ホスト>:28000/blocklist` |
| DNS 許可リスト | `http://<ホスト>:28000/allowlist` |

> [!NOTE]
> DNS フィルタを同じ compose で動かしていても、コンテナ名（`http://proxy280:28000/...`）では取得できないことがある。
> フィルタ取得時の名前解決に Docker の内蔵 DNS を使わない場合があるため。その場合はホストの IP とポートで指定する。

## 許可リストの編集

`allowlist.txt`（git 管理外。`allowlist.example.txt` をコピーして作る）を編集するだけで `/allowlist` に反映される（コンテナに読み取り専用でマウントしており、リクエストごとに読み込む）。DNS フィルタ側へはフィルタの更新タイミングで反映される。ファイルが無い場合 `/allowlist` は 404 を返す。

コンテナは非 root ユーザで動くため、ファイルは他ユーザから読める権限（`644`）にしておく。

## 設定

環境変数で変更できる。

| 変数 | 既定値 | 説明 |
|---|---|---|
| `PROXY280_BLOCKLIST_URL_TEMPLATE` | `https://280blocker.net/files/280blocker_domain_ag_{year}{month:02d}.txt` | 取得元 URL |
| `PROXY280_ALLOWLIST_PATH` | `/config/allowlist.txt`（イメージ内） | 許可リストのパス |
| `PROXY280_HOST` | `0.0.0.0` | 待ち受けアドレス |
| `PROXY280_PORT` | `28000` | 待ち受けポート |
| `PROXY280_TIMEZONE` | `Asia/Tokyo` | 「当月」を判定するタイムゾーン |
| `PROXY280_FETCH_TIMEOUT` | `10` | 取得タイムアウト（秒） |
| `PROXY280_REFRESH_INTERVAL` | `86400` | 上流の更新を確認する間隔（秒） |
| `PROXY280_RETRY_INTERVAL` | `600` | 取得失敗後の再試行間隔（秒） |

## 他の compose に組み込む

DNS フィルタなどと同じ compose に並べる例（ソースを `./proxy280/` に置いた場合）。

```yaml
  proxy280:
    build:
      context: ./proxy280
      target: runtime
    image: proxy280:latest
    container_name: proxy280
    restart: always
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    ports:
      - "28000:28000"
    environment:
      - PROXY280_TIMEZONE=Asia/Tokyo
    volumes:
      - ./proxy280/allowlist.txt:/config/allowlist.txt:ro
```

## 開発

開発コマンドはすべてコンテナ内で実行する。

| コマンド | 内容 |
|---|---|
| `make up` / `make down` / `make logs` | ローカルで起動・停止・ログ表示 |
| `make test` | pytest |
| `make lint` / `make fmt` | ruff によるチェック・整形 |
| `make check` | lint + test |
| `make lock` | `uv.lock` を更新（`make lock LOCK_ARGS=--upgrade` で依存を最新化） |
| `make github-settings` | GitHub リポジトリの設定（許可するアクション・Dependabot・main のルールセット等）を `gh` で適用 |

デプロイなど環境固有のターゲットは `local.mk`（git 管理外）に書くと `Makefile` から読み込まれる。

CI（GitHub Actions）では PR ごとに lint・テストと、runtime イメージのビルド・起動確認を行う。main へは、CI が通った PR でだけ変更できる（ルールセット）。

GitHub Actions・ベースイメージ・`uv.lock` の版は固定しており、Dependabot が毎週更新を PR にする。
