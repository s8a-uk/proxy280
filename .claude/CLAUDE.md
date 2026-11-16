# proxy280

280blocker のドメインリストを固定 URL で配信する LAN 内向けプロキシ。仕様・設定・コマンドは README を参照。

@../README.md

## 依存ライブラリ

- 実行時の依存ライブラリは足さない。`pyproject.toml` の `dependencies` は空のまま保ち、標準ライブラリで書く
- テスト・lint 用のツールは `[dependency-groups] dev` に足す

## 280blocker の利用条件

上流の配布ファイルは「個人用途に限る。営利目的での使用・再配布は禁止」（README 参照）。

- リストをインターネットや第三者に公開・再配布する機能は入れない
- 上流への取得頻度を増やす変更はしない（更新確認は ETag による条件付きリクエストで行う）

## 開発

- 開発コマンドはコンテナ内で実行する。`make check`（lint + test）・`make fmt`・`make lock` を使う
- 変更後は `make check` を通す

## リポジトリに入れないもの

- `allowlist.txt`・`local.mk` は環境固有のファイル（git 管理外）。サンプルは `allowlist.example.txt`
- IP アドレス・ホスト名・デプロイ先などの環境固有の情報
- 特定の DNS フィルタ製品の名前。README・コード・コミットメッセージでは「DNS フィルタ」と書く
