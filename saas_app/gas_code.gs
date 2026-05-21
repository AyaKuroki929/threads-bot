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

function onFormSubmit(e) {
  var responses = e.namedValues;

  var salonName    = (responses['サロン名'] || ['不明'])[0].trim();
  var ownerName    = (responses['オーナー名（投稿で使うお名前）'] || ['不明'])[0].trim();
  var threadsId    = (responses['Threadsのアカウント名（@から始まるID）'] || ['不明'])[0].trim();
  var customerId   = (responses['_customer_id'] || [''])[0].trim();
  var instagramUrl = (responses['インスタグラムのURL'] || [''])[0].trim();

  // instagram_url を Supabase に保存
  if (customerId && instagramUrl) {
    try {
      UrlFetchApp.fetch('https://saas.shikisai.work/api/save-form', {
        method: 'post',
        headers: { 'Content-Type': 'application/json' },
        payload: JSON.stringify({ customer_id: customerId, instagram_url: instagramUrl }),
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
