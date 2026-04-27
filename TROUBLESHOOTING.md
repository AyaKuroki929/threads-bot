# 困った時マニュアル

## 1. 投稿が出てない気がする

### 確認手順
ターミナルで：
```
cd ~/threads_bot
gh run list --workflow=post.yml --limit 10
```
直近10件の状態が見えます。`success` が並んでいれば正常。

### Threadsアプリで bemolle_diet のフィードを確認
出てれば成功。出てなければ次へ。

### 手動で投稿補完
```
gh workflow run post.yml -f slot=morning -f dry_run=false   # 朝
gh workflow run post.yml -f slot=noon -f dry_run=false      # 昼
gh workflow run post.yml -f slot=evening -f dry_run=false   # 夜
```
1〜2分で投稿されます。

---

## 2. 「🚨 [日付] slot 投稿未確認」のメール / Issueが来た

ハートビートが「投稿が記録されてない」と検知した状態。

### 対応
1. Threadsアプリで実際に投稿が出ているか確認
2. 出ていれば：単に記録がズレているだけ → Issueをcloseで終了
3. 出ていなければ：上の「手動で投稿補完」コマンドを実行

---

## 3. ログイン切れ（cookie expired）エラーが出た

`Login required` 系のエラーがログに出たら：

```
cd ~/threads_bot
python3 extract_cookies2.py
gh secret set THREADS_SESSION < session.json
```

これで GitHub の Secret が更新され、次回投稿から復帰します。
頻度: 年に数回程度。

---

## 4. ネタが枯渇した（CIが「ネタ枯渇」エラー）

```
cd ~/threads_bot
./regen.sh
```

regen.sh が新ネタ生成→GitHubにpushまで自動でやります。
普段は LaunchAgent が自動で走るので、これを手動実行することはほぼ無い。

---

## 5. ステータス確認コマンド集

### 在庫（残ネタ本数）
```
cd ~/threads_bot
python3 -c "import json; p=json.load(open('posts.json')); u=json.load(open('used_posts.json')); [print(f'{s}: {len(p[s])-len(u.get(s,[]))}/{len(p[s])}') for s in ['morning','noon','evening']]"
```

### 直近の投稿run（成否）
```
gh run list --workflow=post.yml --limit 10
```

### 直近のheartbeat run
```
gh run list --workflow=heartbeat.yml --limit 5
```

### 失敗時の詳細ログ
```
gh run view <run_id> --log-failed
```

---

## 6. 完全に止めたい場合

すべてのscheduleを停止:
```
gh workflow disable "Threads Auto Post"
gh workflow disable "Heartbeat - Verify Post Success"
```

再開:
```
gh workflow enable "Threads Auto Post"
gh workflow enable "Heartbeat - Verify Post Success"
```

---

## 7. リポジトリ直接見たい

ブラウザで:
- https://github.com/AyaKuroki929/threads-bot
- Actions タブ → 直近のjob実行履歴
- Issues タブ → ハートビートが立てたアラート
