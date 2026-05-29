// ============================================================
// 🟢 本番稼働中：とうこさんSaaS フォーム送信スクリプト（ライブ全文・これ1枚で完結）
// ------------------------------------------------------------
// ⚠️ これは clasp 管理外。Apps Scriptエディタに手動で貼って運用する本番コード。
//    更新するときは【このファイルを全選択コピー → エディタの中身を全消し → 丸ごと貼り付け → 保存】。
//    部分差し替えはしない（事故防止）。
//    - clasp が指すプロジェクト(.clasp.json) = gas_code.gs（重複の設定関数。フォーム送信では未使用）
//    - フォーム送信で実際に動くのは【このスクリプト】
//    含まれる関数: findIdx / moveToAfter / step1〜4(フォーム作成用・通常は触らない) /
//                  normalizeThreadsId_ / notifyAdmin_ / onFormSubmit(本体)
// ============================================================

function findIdx(form, title) {
  var items = form.getItems();
  for (var i = 0; i < items.length; i++) {
    if (items[i].getTitle() === title) return i;
  }
  return -1;
}

function moveToAfter(form, afterTitle) {
  var idx = findIdx(form, afterTitle);
  if (idx >= 0) form.moveItem(form.getItems().length - 1, idx + 1);
}

function step1_addQuestions() {
  var form = FormApp.openById('1bvPkeQl6sC-BOLDF3n7QrCMuTXCtKKQ3oEzd84_CbDY');

  form.addTextItem()
    .setTitle('投稿で自分を何と呼びますか？')
    .setHelpText('例：私 / わたし / 俺 / 田中（自分の名前）\n投稿に「私が試した」のように使います');
  moveToAfter(form, 'オーナー名（投稿で使うお名前）');

  form.addTextItem()
    .setTitle('サロンのオープン年（何年目ですか）')
    .setHelpText('例：2018年（8年目）\n投稿に「〇年の実績」と使います');
  moveToAfter(form, '業界経歴・年数');

  form.addTextItem()
    .setTitle('スタッフ人数と特徴（一人運営の場合は「一人」と記入）')
    .setHelpText('例：一人運営（オーナーのみ担当） / スタッフ2名（1名は元大手エステ勤務）');
  moveToAfter(form, 'サロンのオープン年（何年目ですか）');

  form.addTextItem()
    .setTitle('新規のお客様が最初に来る一番多い理由・悩みは何ですか？')
    .setHelpText('投稿の1行目のフックに使います\n例：何度ダイエットしても続かない、産後に体型が戻らない、顔のたるみが気になり始めた');
  moveToAfter(form, 'メインターゲット（年代・性別・どんな悩みを持つ人）');

  form.addMultipleChoiceItem()
    .setTitle('価格を投稿に記載してもOKですか？')
    .setChoiceValues(['はい（具体的な金額を投稿に出してOK）', 'いいえ（詳しくはDMまたはHPへ誘導する）', '体験・初回コースの価格のみOK'])
    .setRequired(true);
  moveToAfter(form, '提供メニューと価格帯（箇条書きでOK）');

  form.addMultipleChoiceItem()
    .setTitle('お客様の声・体験談を投稿に使ってもOKですか？（匿名・お声をいただきました形式）')
    .setChoiceValues(['はい（匿名でお客様の感想・変化を投稿に使ってOK）', 'いいえ（体験談は使わず一般的な内容のみ）'])
    .setRequired(true);
  moveToAfter(form, '印象的なお客様の声・体験談（名前不要・1～3件）');

  form.addTextItem()
    .setTitle('取得した資格・受講した研修・認定など（あれば）')
    .setHelpText('なければ空欄でOK。信頼性を高める投稿素材になります\n例：リンパドレナージュ資格、アロマテラピー検定1級');
  moveToAfter(form, 'なぜこのサロンを作ったか');

  Logger.log('7問追加完了！');
}

function step2_updateHelp() {
  var form = FormApp.openById('1bvPkeQl6sC-BOLDF3n7QrCMuTXCtKKQ3oEzd84_CbDY');
  var items = form.getItems();
  for (var i = 0; i < items.length; i++) {
    var t = items[i].getTitle();
    if (t === 'オーナー名（投稿で使うお名前）') {
      items[i].setHelpText('例：田中美咲（ニックネームや苗字のみでも可）');
    } else if (t === '予約はどこから受けていますか？（メインの予約先）') {
      items[i].setHelpText('投稿末尾のCTAに使います\n例：LINE公式アカウント、ホットペッパービューティー、電話、自社サイト');
    } else if (t === '一番の売りメニュー・最も結果が出やすい施術') {
      items[i].setHelpText('例：痩身EMSコース（3ヶ月10回でウエスト-8cm平均）');
    } else if (t === '他サロンとの違い・このサロンならではの施術やこだわり') {
      items[i].setHelpText('例：栄養指導と施術を組み合わせている、全員にオーダーメイドの施術計画を立てる');
    } else if (t === 'サロンを一言で表すキャッチコピー（あれば）') {
      items[i].setHelpText('なければ空欄でOK\n例：根本から変える8週間、食事制限なしで体を変えるサロン');
    } else if (t === '途中で来なくなるお客様に多いパターン・理由（わかる範囲でOK）') {
      items[i].setHelpText('なければ空欄でOK\n例：3〜5回通って変化を感じる前に諦める、費用面で続けられなくなる');
    } else if (t === '自分の発信スタイルのNGライン（やりたくない表現・雰囲気・言葉づかい）') {
      items[i].setHelpText('例：強引な売り込み感が出る文章、他サロンを下げる比較表現、馴れ馴れしいタメ口');
    } else if (t === 'オーナー自身について投稿に出してOKな情報・出してほしくない情報') {
      items[i].setHelpText('例（OK）：子どもがいること、趣味の料理、地元が大阪\n例（NG）：家族の詳細、個人が特定できる情報');
    } else if (t === 'なぜこのサロンを作ったか') {
      items[i].setHelpText('例：自分が20年ダイエットに失敗し、根本原因に気づいてから13kg減。同じ悩みを持つ人を助けたくて開業した');
    }
  }
  Logger.log('ヘルプテキスト更新完了！');
}

function step3_addDiffQuestions() {
  var form = FormApp.openById('1bvPkeQl6sC-BOLDF3n7QrCMuTXCtKKQ3oEzd84_CbDY');

  form.addTextItem()
    .setTitle('オーナーの口癖・よく使う言葉・フレーズ')
    .setHelpText('投稿文にオーナーらしさを出すために使います。\n例：「ほんとうに」「〜って大事だなって」「実はですね」「正直に言うと」など\nなければ空欄でOK');
  moveToAfter(form, 'サロンを一言で表すキャッチコピー（あれば）');

  form.addTextItem()
    .setTitle('サロン独自のキーワード・コンセプトワード')
    .setHelpText('他のサロンと被らない独自の表現・造語・コンセプト名など。\n例：「芯から変わる施術」「ととのいエステ」「インナービューティーケア」\nなければ空欄でOK');
  moveToAfter(form, 'オーナーの口癖・よく使う言葉・フレーズ');

  Logger.log('差別化用2問を追加しました！');
}


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

  // メールアドレス（customer_id空のときStripe逆引きに使う）
  var email = (responses['メールアドレス'] || [''])[0].trim();
  if (!email) {
    // 項目名が違う場合の保険：@とドットを含む最初の回答をメールとみなす
    for (var key in responses) {
      var v = (responses[key] || [''])[0] || '';
      if (v.indexOf('@') > 0 && v.indexOf('.') > 0) { email = v.trim(); break; }
    }
  }

  // ⭐ #4対策：必須項目の存在チェック（フォーム項目名が変わると検知）
  // ※ _customer_id は空でもメール逆引きで復元するため、ここでは検知対象から外す
  var requiredFields = [
    'サロン名',
    'オーナー名（投稿で使うお名前）',
    'Threadsのアカウント名（@から始まるID）'
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

  // ⭐ #2対策 + メール逆引きフォールバック：
  // save-form を必ず呼ぶ（customer_idが空でも email で Stripe逆引きして復元）。
  // → フォームをクリアされても・別リンクで回答されても、メールさえ一致すれば自動で紐付く。
  // → ついでに instagram_url と expected_threads_id を Supabase に保存（callback.pyの照合用）。
  var recovered = false;
  try {
    var resp = UrlFetchApp.fetch('https://saas.shikisai.work/api/save-form', {
      method: 'post',
      headers: { 'Content-Type': 'application/json' },
      payload: JSON.stringify({
        customer_id: customerId,
        email: email,
        instagram_url: instagramUrl,
        expected_threads_id: threadsId
      }),
      muteHttpExceptions: true
    });
    var j = JSON.parse(resp.getContentText() || '{}');
    if (j && j.customer_id) {
      if (!customerId) recovered = true;   // 空だったのが埋まった＝復元成功
      customerId = j.customer_id;
    }
  } catch (err) {
    console.error('save-form失敗:', err);
  }

  // 逆引きしても customer_id が取れなければアラート
  if (!customerId) {
    notifyAdmin_(
      '🚨 customer_id未取得！\n\n' +
      'フォーム回答に _customer_id が無く、メール(' + (email || '不明') + ')での\n' +
      'Stripe逆引きも失敗しました（メール不一致の可能性）。\n\n' +
      'サロン名: ' + salonName + '\n' +
      'Stripeダッシュボードで顧客IDを確認し手動対応してください。'
    );
  }

  var sendStepUrl = customerId
    ? 'https://saas.shikisai.work/api/send-step?customer_id=' + customerId
    : '⚠️ customer_id未取得（Stripeダッシュボードで確認してください）';

  var recoveredLine = recovered ? '\n♻️ customer_idはメールから自動復元しました' : '';

  var instaLine = instagramUrl ? '\nInstagram：' + instagramUrl : '';

  var message =
    '📋 とうこさん フォーム回答あり！\n\n' +
    'サロン名：' + salonName + '\n' +
    'オーナー名：' + ownerName + '\n' +
    'Threads ID：' + threadsId +
    instaLine + recoveredLine + '\n\n' +
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
