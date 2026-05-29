// ============================================================
// 🟢 本番稼働中：とうこさんSaaS フォーム送信ハンドラ（ライブ版）
// ------------------------------------------------------------
// ⚠️ これは clasp 管理外。Apps Scriptエディタに手動で貼り付けて運用している
//    「フォーム送信トリガー」の本番コード。
//    - clasp が指すプロジェクト(.clasp.json) = gas_code.gs（step1/2/3＋旧onFormSubmit、validationなし）
//    - 実際にフォーム送信で動くのは【このコード】（validation＋Threads ID正規化＋expected_threads_id保存）
//    本番を更新したら必ずこのファイルも更新し git commit すること（onlineが飛んでも復元できるように）。
//    復元元バックアップ: ~/.claude/file-history/.../9812a726a18ea24a@v6 (2026-05-29)
// ============================================================

function step4_updateHelpText2() {
  var form = FormApp.openById('1bvPkeQl6sC-BOLDF3n7QrCMuTXCtKKQ3oEzd84_CbDY');
  var h = {};
  h['所在地（最寄り駅・徒歩時間）'] = '例：○○駅 徒歩3分（△△市□□区）';
  h['提供メニューと価格帯（箇条書きでOK）'] = '例：フェイシャル60分 ¥8,000〜 / 全身リンパ90分 ¥12,000〜 / 初回体験 ¥3,000';
  h['お客様の具体的な変化・実績（数字があれば）'] = '例：3回で肌のトーンが変わったとのお声多数 / 継続6ヶ月でたるみが気にならなくなった';
  h['印象的なお客様の声・体験談（名前不要・1～3件）'] = '例：「他サロンと全然違う」「毎回リラックスできる」「友人に肌がキレイになったと言われた」';
  h['お客様がよく比較検討する他サービスや選択肢（ジム・通販・他サロン等）'] = '例：他エステ、美顔器・家庭用マッサージ、皮膚科、ネットで買えるスキンケア';
  h['お客様がよくやりがちな間違い・勘違い（3つ程度）'] = '例：自己流マッサージで逆にたるむ、洗顔のしすぎで肌荒れ、高いコスメを買えばいいと思っている';
  h['過去の失敗・コンプレックス（「実はこんなだった」という意外な過去）'] = '例：10代から肌荒れで悩み多くのコスメを試したが改善しなかった / エステに通っても結果が出なかった時期があった';
  h['転換点・気づき（何をきっかけに変わったか）'] = '例：本当の原因は施術より生活習慣にあると気づいた / 正しいケア方法を学んで初めて肌が安定した';
  h['自分自身で出た成果・結果'] = '例：正しいケアで毛穴の開きが改善・化粧ノリが明らかに変わった / 肌の乾燥が解消され化粧品代が1/3に';
  h['オーナー自身が毎日続けているケア・習慣（業種に関連するもの）'] = '例：毎朝の丁寧な洗顔と保湿、週1の自宅フェイシャルケア';
  h['趣味・ライフスタイル（オーナー自身・業種と無関係でもOK）'] = '例：料理・読書・近所の公園でのウォーキング';
  h['お客様への向き合い方・こだわり'] = '例：毎回肌状態を確認して施術内容を調整する / 施術後のホームケアも毎回丁寧に伝える';
  h['サロンとして「言いたくないこと」「NGワード」'] = '例：「絶対」「100%」「劇的変化」などの誇大表現 / 他サロンを名指しする表現';
  h['過去にクレームになったこと・誤解されやすいこと'] = '例：1回で変わると思って来たが効果が出なかった / コースによって施術内容が違うことが伝わっていなかった';
  h['投稿に含めてよい成分名・施術名・商品名（ここに書いたもの以外は使いません）'] = '例：ハイフ、EMS、リンパドレナージュ（ここに書いた以外の成分名・商品名は使わない）';
  h['特に力を入れてほしい投稿スタイル（当てはまるものをすべて記入）'] = '例：教育系投稿（間違ったケアを正す）、ストーリー投稿（オーナーの体験談）';
  h['投稿の最終ゴールとして最も重視すること（1つ）'] = '例：体験コースの予約 / LINE友達追加 / ホームページへの誘導';
  h['特に集客を強化したい季節・月（あれば）'] = '例：春（3〜4月）の新生活シーズン / 夏前（5〜6月）の紫外線ダメージケア訴求';

  form.getItems().forEach(function(item) {
    var t = item.getTitle();
    if (h[t]) {
      try { item.setHelpText(h[t]); Logger.log('更新: ' + t); }
      catch(ex) { Logger.log('スキップ: ' + t); }
    }
  });
  Logger.log('完了！');
}

// ============================================================
// 📋 とうこさん - フォーム回答時のLINE通知＋Threads ID自動正規化
// ============================================================

// Threads ID正規化（全角＠・半角@・大文字・前後スペース・全角英数を吸収）
function normalizeThreadsId_(raw) {
  if (!raw) return '';
  var s = String(raw).trim();
  var map = {
    '０':'0','１':'1','２':'2','３':'3','４':'4','５':'5','６':'6','７':'7','８':'8','９':'9',
    'Ａ':'A','Ｂ':'B','Ｃ':'C','Ｄ':'D','Ｅ':'E','Ｆ':'F','Ｇ':'G','Ｈ':'H','Ｉ':'I','Ｊ':'J',
    'Ｋ':'K','Ｌ':'L','Ｍ':'M','Ｎ':'N','Ｏ':'O','Ｐ':'P','Ｑ':'Q','Ｒ':'R','Ｓ':'S','Ｔ':'T',
    'Ｕ':'U','Ｖ':'V','Ｗ':'W','Ｘ':'X','Ｙ':'Y','Ｚ':'Z',
    'ａ':'a','ｂ':'b','ｃ':'c','ｄ':'d','ｅ':'e','ｆ':'f','ｇ':'g','ｈ':'h','ｉ':'i','ｊ':'j',
    'ｋ':'k','ｌ':'l','ｍ':'m','ｎ':'n','ｏ':'o','ｐ':'p','ｑ':'q','ｒ':'r','ｓ':'s','ｔ':'t',
    'ｕ':'u','ｖ':'v','ｗ':'w','ｘ':'x','ｙ':'y','ｚ':'z',
    '．':'.','＿':'_','－':'-'
  };
  s = s.replace(/[０-９Ａ-Ｚａ-ｚ．＿－]/g, function(c) { return map[c] || c; });
  while (s.length > 0 && (s.charAt(0) === '@' || s.charAt(0) === '＠')) {
    s = s.substring(1);
  }
  return s.trim().toLowerCase();
}

// Claude通知Botへ管理者警告を送る
function notifyAdmin_(text) {
  var token = PropertiesService.getScriptProperties().getProperty('LINE_CHANNEL_ACCESS_TOKEN');
  if (!token) return;
  try {
    UrlFetchApp.fetch('https://api.line.me/v2/bot/message/broadcast', {
      method: 'post',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
      payload: JSON.stringify({ messages: [{ type: 'text', text: text }] }),
      muteHttpExceptions: true
    });
  } catch (err) { console.error('notifyAdmin_ 失敗:', err); }
}

function onFormSubmit(e) {
  var responses = e.namedValues;

  // ⭐ #4対策：必須項目の存在チェック（フォーム項目名が変わると検知）
  var requiredFields = [
    'サロン名',
    'オーナー名（投稿で使うお名前）',
    'Threadsのアカウント名（@から始まるID）',
    '_customer_id'
  ];
  var missing = requiredFields.filter(function(f) {
    return !responses[f] || !responses[f][0];
  });
  if (missing.length > 0) {
    notifyAdmin_(
      '🚨 フォーム必須項目が見つからない！\n\n' +
      '欠落: ' + missing.join(', ') + '\n\n' +
      'フォームの項目名が変更された可能性があります。\n' +
      'GASコードの項目名と一致しているか確認してください。'
    );
  }

  var salonName    = (responses['サロン名'] || ['不明'])[0].trim();
  var ownerName    = (responses['オーナー名（投稿で使うお名前）'] || ['不明'])[0].trim();
  var rawThreadsId = (responses['Threadsのアカウント名（@から始まるID）'] || ['不明'])[0].trim();
  var threadsId    = normalizeThreadsId_(rawThreadsId);
  var customerId   = (responses['_customer_id'] || [''])[0].trim();
  var instagramUrl = (responses['インスタグラムのURL'] || [''])[0].trim();

  // ⭐ #1対策：表記揺れがあればスプレッドシートを正規化された値で自動上書き
  try {
    if (rawThreadsId && rawThreadsId !== threadsId && threadsId !== '') {
      var sheet = e.range.getSheet();
      var row = e.range.getRow();
      var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
      var colIdx = headers.indexOf('Threadsのアカウント名（@から始まるID）');
      if (colIdx >= 0) {
        sheet.getRange(row, colIdx + 1).setValue(threadsId);
        console.log('Threads ID正規化: ' + rawThreadsId + ' → ' + threadsId);
      }
    }
  } catch (err) {
    console.error('Threads ID正規化失敗:', err);
  }

  // ⭐ #2対策：instagram_url と expected_threads_id を Supabase に保存
  // → callback.py で OAuth したアカウントが一致するか確認するため
  if (customerId) {
    try {
      UrlFetchApp.fetch('https://saas.shikisai.work/api/save-form', {
        method: 'post',
        headers: { 'Content-Type': 'application/json' },
        payload: JSON.stringify({
          customer_id: customerId,
          instagram_url: instagramUrl,
          expected_threads_id: threadsId
        }),
        muteHttpExceptions: true
      });
    } catch (err) {
      console.error('save-form失敗:', err);
    }
  }

  var sendStepUrl = customerId
    ? 'https://saas.shikisai.work/api/send-step?customer_id=' + customerId
    : '⚠️ customer_id未取得（Stripeダッシュボードで確認してください）';

  var instaLine = instagramUrl ? '\nInstagram：' + instagramUrl : '';

  var message =
    '📋 とうこさん フォーム回答あり！\n\n' +
    'サロン名：' + salonName + '\n' +
    'オーナー名：' + ownerName + '\n' +
    'Threads ID：' + threadsId +
    instaLine + '\n\n' +
    '【やること】\n' +
    '① MetaでThreadsテスター追加 →\n' +
    'https://developers.facebook.com/apps/1497479218824264/roles/roles/\n\n' +
    '② テスター追加後にこちらをタップ ↓\n' +
    '（タップするとSTEP1/2がクライアントのLINEに自動送信されます）\n' +
    sendStepUrl;

  var token = PropertiesService.getScriptProperties().getProperty('LINE_CHANNEL_ACCESS_TOKEN');
  if (!token) {
    console.error('LINE_CHANNEL_ACCESS_TOKEN not set');
    return;
  }

  UrlFetchApp.fetch('https://api.line.me/v2/bot/message/broadcast', {
    method: 'post',
    headers: {
      'Authorization': 'Bearer ' + token,
      'Content-Type': 'application/json'
    },
    payload: JSON.stringify({
      messages: [{ type: 'text', text: message }]
    }),
    muteHttpExceptions: true
  });
}
