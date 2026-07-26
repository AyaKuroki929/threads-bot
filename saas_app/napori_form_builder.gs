/**
 * ============================================================
 * ナポリ（napori_mark）｜Threads自動投稿 コンサル用ヒアリングシート
 *   ビルダー（createNaporiForm）＋ 回答ハンドラ（onNaporiFormSubmit）
 * ------------------------------------------------------------
 * サロン用とは別。個人事業主向けコンサル「弱者のマーケティング」専用。
 * 誘導ゴール＝Instagramのフォロー（LINEではない）。感情・物語ドリブン。
 *
 * 【使い方】
 *  1) https://script.google.com → 「新しいプロジェクト」
 *  2) このファイルの中身を全部コピーして貼り付け → 保存（プロジェクト名は何でもOK）
 *  3) 上部の関数選択を「createNaporiForm」にして ▶実行 → 権限を承認
 *  4) メニュー「実行数 / 実行ログ」を開き、最後に出る
 *       PUBLISHED_URL / SPREADSHEET_ID / CUSTOMER_ID_ENTRY をコピーしてClaude（彩さん）に渡す
 *  5) このプロジェクトの「プロジェクトの設定 → スクリプト プロパティ」で
 *       LINE_CHANNEL_ACCESS_TOKEN = （とうこさん公式LINEのアクセストークン）を1つ追加
 *
 * ※ createNaporiForm は一度だけ実行する（再実行すると別フォームがもう1つできる）。
 * ============================================================
 */

function createNaporiForm() {
  var intro = [
    'ご登録ありがとうございます。',
    'このシートは、あなたのことをシステムに深く知ってもらい、Threads（スレッズ）自動投稿の質を上げるためのシートです。',
    'AIがこの回答をもとに、あなたにしか書けない投稿を毎日自動でお届けします。',
    '',
    'このアカウントのゴールは、Threadsの投稿からInstagramをフォローしてもらうことです。',
    'だからこそ、上手い文章より「あなたの思い・過去・人柄」がそのまま出る回答が一番効きます。',
    'うまく書こうとしなくて大丈夫です。友達に話すように、思っていることをそのまま書いてください。',
    '正直に・具体的に・感情そのままで書いていただくほど、刺さる投稿になります。',
    '',
    '※印のある質問は回答必須です。思いつかない項目は「特になし」でOK。最後に自由記入欄があります。'
  ].join('\n');

  var form = FormApp.create('ナポリ｜Threads自動投稿 ヒアリングシート');
  form.setDescription(intro);
  form.setAllowResponseEdits(true);
  form.setCollectEmail(false);

  // ---- セクション0：基本情報 ----
  sec_(form, 'セクション0：基本情報');
  txt_(form, 'お名前', 'ご登録・ご連絡用です', true);
  txt_(form, '投稿で呼ばれたいお名前・呼び方', '例：ナポリ　「ナポリです」のように投稿の冒頭で使います', true);
  txt_(form, 'ThreadsのアカウントID（@から始まるID）', '例：@napori_mark', true);
  txt_(form, 'InstagramのURL（このThreadsで一番フォローしてほしい先）', '例：https://www.instagram.com/napori_mark/', true);
  txt_(form, 'lit.link・その他のリンク', 'lit.link / 公式LINE / セミナー申込ページ など。なければ「特になし」', true);
  txt_(form, 'メールアドレス', 'ご登録・ご連絡用です', true);
  txt_(form, 'あなたの事業を一言でいうと', '例：個人事業主向けの「弱者のマーケティング」コンサル', true);

  // ---- セクション1：あなたの原点（このシートの心臓部）----
  sec_(form, 'セクション1：あなたの原点（このシートの心臓部）');
  par_(form, 'この仕事を始める前、一番どん底だった頃の話', '例：手取り13万円の美容師だった頃。金額・状況・その時どんな気持ちだったかを、具体的に', true);
  par_(form, 'あの頃、一番しんどかった・悔しかった瞬間', '言われたセリフや、その場の情景つきで書けると強いです。読み手が一番心を動かされる部分です', true);
  par_(form, '何がきっかけで変わり始めたか（転機の出来事）', '例：あの人の一言／あの本／あの失敗。具体的な出来事を', true);
  par_(form, '変わるまでに実際にやったこと・した失敗', 'うまくいった話より、もがいた過程・失敗こそ刺さります', true);
  par_(form, 'どん底だった頃の自分に、今なら何と声をかけますか', '一言でもOKです', true);
  par_(form, 'なぜ今、この仕事をやっているのか（お金以外の理由）', 'あなたの一番の「なぜ」。ここが投稿全体の軸になります', true);

  // ---- セクション2：届けたい相手（"弱者"とは誰か）----
  sec_(form, 'セクション2：届けたい相手（"弱者"とは誰か）');
  par_(form, 'どんな人を助けたいか（具体的な人物像とその人の悩み）', '例：頑張っているのに集客できない個人事業主／SNSが伸びずに諦めかけている人。年代・職種・状況まで具体的に', true);
  par_(form, '「弱者のマーケティング」を一言でいうと', 'あなたの言葉で。難しく考えず、いつも人に説明している言い方でOK', true);
  par_(form, 'なぜ"強者"ではなく"弱者"にこだわるのか', 'あなたの信念・過去とつながっている部分だと思います。そのまま書いてください', true);
  par_(form, 'お客様にどうなってほしいか（Before → After）', '例：バズらせようと消耗していた人が、交流会×SNSで毎月安定して集客できるようになる', true);
  par_(form, '実際に変わった受講生・お客様のエピソード', '名前不要。数字（売上・集客数）と、その人の感情・変化の両方があると強いです', true);
  mc_(form, '受講生・お客様の成果や声を投稿に使ってもOKですか？', ['はい（匿名で使ってOK）', 'いいえ（一般的な内容のみ）'], true);

  // ---- セクション3：あなたの信念・日々思っていること（日常投稿の素材）----
  sec_(form, 'セクション3：あなたの信念・日々思っていること');
  par_(form, '仕事で絶対に譲らない信念・口ぐせ', '例：弱いやつ全員こっち来い。あなたが普段から言っていること', true);
  par_(form, '世間の常識で「それ、違うと思う」こと（逆張り・持論）', '例：アポなんかするな／バズを狙うな。投稿の"攻めのフック"になります。いくつでもどうぞ', true);
  par_(form, '最近ムカついたこと・心が動いたこと', '日々の投稿ネタになります。仕事でもプライベートでもOK', true);
  par_(form, '365日ピンクスーツの理由／自分のキャラをどう見せたいか', 'あなたのキャラ設定・見せ方。読者にどんな人だと思われたいか', true);
  par_(form, '投稿に出してOKな人間味・プライベート', '弱さ・失敗・日常も歓迎です。逆に出してほしくないことは次のセクションで', true);

  // ---- セクション4：サービスと実績（信頼の裏付け）----
  sec_(form, 'セクション4：サービスと実績');
  par_(form, '提供しているものと、対象・価格帯', '無料セミナー／個別相談／カリキュラム 等。箇条書きでOK', true);
  par_(form, 'サービスで教える具体的な中身', '例：商品サービスの設計／新規集客／時間の使い方／お金の使い方／メンタル・マインドの目標設定／コーチング など', true);
  par_(form, '投稿に出してよい実績数字', '例：年商8桁／受講生の成果／セミナー動員数 など。誇張はしません', true);
  par_(form, '「交流会×SNSで毎月の集客を仕組み化」を、あなたの言葉で説明すると', 'サービスの核。いつも説明している言い方でOK', true);
  mc_(form, '価格を投稿に出してもOKですか？', ['はい（具体的な金額を出してOK）', 'いいえ（Instagram・LINEへ誘導する）', '一部のみOK（無料セミナー・体験など）'], true);

  // ---- セクション5：イベント告知（くどくならない形で）----
  sec_(form, 'セクション5：イベント告知');
  par_(form, '定期的にやっているイベント・セミナーの種類と頻度', '例：毎月の交流会／無料セミナー／個別相談会。なければ「特になし」', true);
  par_(form, '直近で告知したいイベント（あれば）', '日時・場所・申込先URLも書いてください。なければ「特になし」', true);
  par_(form, 'イベントに来た人にどうなってほしいか', '告知投稿のトーンを決めるために使います', true);

  // ---- セクション6：誘導・ゴール ----
  sec_(form, 'セクション6：投稿のゴール（誘導先）');
  chk_(form, '投稿の最後で読者にしてほしい行動（複数選択可）', [
    'Instagramをフォローしてほしい（メインのゴール）',
    'lit.link・プロフィールのリンクを見てほしい',
    'DMを送ってほしい',
    'コメントで反応してほしい',
    '特に誘導はせず、まずは内容を届けたい'
  ], true);
  txt_(form, 'CTAで誘導したいURL（Instagram / lit.link など）', '例：https://www.instagram.com/napori_mark/', true);
  mc_(form, 'CTA（誘導）を出す頻度', ['毎回出してOK', '2〜3回に1回くらい', 'たまにでいい（押し売り感を出したくない）'], true);

  // ---- セクション7：トーン・NG ----
  sec_(form, 'セクション7：トーン・NG');
  txt_(form, '投稿で自分を何と呼びますか（一人称）', '例：僕 / 俺 / 私', true);
  par_(form, '語尾・口調のクセ、使ってほしい決めゼリフ・口ぐせ', '例：「〜っしょ」「大丈夫、僕も弱いから」。あなたらしさが出る言い回し', true);
  mc_(form, '絵文字をどうしますか？', ['使わない（または最小限）', '適度に使う', 'たくさん使う'], true);
  par_(form, '触れてほしくない過去・NGワード・言いたくないこと', '例：「絶対」「100%」などの誇大表現。特定の過去。なければ「特になし」', true);
  par_(form, '下げたくない相手（名指しで批判したくない手法・人）', '例：特定の集客手法や他のコンサルを否定する表現はしない。なければ「特になし」', true);

  // ---- 最後：自由記入（任意）----
  sec_(form, '最後に');
  par_(form, 'それ以外で伝えておきたいこと（自由記入）', '上のどの質問にも当てはまらないけれど投稿に活かしてほしいこと・大事にしている考え・エピソードなど、なんでも自由にどうぞ。なければ空欄でOKです', false);

  // ---- 隠し：customer_id（自動入力欄。空でも送信可）----
  var cid = form.addTextItem()
    .setTitle('_customer_id')
    .setHelpText('※システム用の自動入力欄です。空欄のままで大丈夫です（触らずに送信してください）')
    .setRequired(false);

  // ---- 回答先スプレッドシート作成＆連携 ----
  var ss = SpreadsheetApp.create('ナポリ｜Threads フォーム回答');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());

  // ---- onFormSubmit トリガー設置 ----
  removeNaporiTriggers_();
  ScriptApp.newTrigger('onNaporiFormSubmit').forForm(form).onFormSubmit().create();

  // ---- _customer_id の entry ID をプリフィルURLから抽出 ----
  var entry = '(取得失敗。下のPREFILLED_URLを見てください)';
  try {
    var pre = form.createResponse().withItemResponse(cid.createResponse('CIDTEST')).toPrefilledUrl();
    var m = pre.match(/entry\.(\d+)=CIDTEST/);
    if (m) entry = 'entry.' + m[1];
    Logger.log('PREFILLED_URL : ' + pre);
  } catch (err) {
    Logger.log('プリフィルURL生成エラー: ' + err);
  }

  Logger.log('================ コピーしてClaudeへ渡す ================');
  Logger.log('PUBLISHED_URL    : ' + form.getPublishedUrl());
  Logger.log('EDIT_URL         : ' + form.getEditUrl());
  Logger.log('SPREADSHEET_ID   : ' + ss.getId());
  Logger.log('SPREADSHEET_URL  : ' + ss.getUrl());
  Logger.log('CUSTOMER_ID_ENTRY: ' + entry);
  Logger.log('======================================================');
}

// ============================================================
// 回答時ハンドラ：管理者LINE通知＋Threads ID正規化＋save-form
//   新運用（2026-07-17〜）＝テスター追加・STEP1なし。send-stepタップのみ。
// ============================================================
function onNaporiFormSubmit(e) {
  var r = e.namedValues;

  var email = first_(r, 'メールアドレス');
  if (!email) {
    for (var k in r) {
      var v = (r[k] || [''])[0] || '';
      if (v.indexOf('@') > 0 && v.indexOf('.') > 0) { email = v.trim(); break; }
    }
  }

  var name    = first_(r, 'お名前') || first_(r, '投稿で呼ばれたいお名前・呼び方') || '不明';
  var rawTid  = first_(r, 'ThreadsのアカウントID（@から始まるID）') || '不明';
  var tid     = normalizeThreadsId_(rawTid);
  var insta   = first_(r, 'InstagramのURL（このThreadsで一番フォローしてほしい先）');
  var customerId = first_(r, '_customer_id');

  var required = ['お名前', 'ThreadsのアカウントID（@から始まるID）'];
  var missing = required.filter(function (f) { return !r[f] || !r[f][0]; });
  if (missing.length > 0) {
    notifyLine_('🚨 ナポリフォーム 必須項目が見つからない！\n欠落: ' + missing.join(', ') + '\nフォーム項目名が変わった可能性があります。');
  }

  // Threads ID正規化を書き戻し
  try {
    if (rawTid && rawTid !== tid && tid !== '') {
      var sheet = e.range.getSheet();
      var row = e.range.getRow();
      var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
      var ci = headers.indexOf('ThreadsのアカウントID（@から始まるID）');
      if (ci >= 0) sheet.getRange(row, ci + 1).setValue(tid);
    }
  } catch (err) { console.error('正規化失敗:', err); }

  // save-form（customer_id空でも email で Stripe逆引きして復元）
  var recovered = false;
  try {
    var resp = UrlFetchApp.fetch('https://saas.shikisai.work/api/save-form', {
      method: 'post',
      headers: { 'Content-Type': 'application/json' },
      payload: JSON.stringify({ customer_id: customerId, email: email, expected_threads_id: tid, instagram_url: insta }),
      muteHttpExceptions: true
    });
    var j = JSON.parse(resp.getContentText() || '{}');
    if (j && j.customer_id) { if (!customerId) recovered = true; customerId = j.customer_id; }
  } catch (err) { console.error('save-form失敗:', err); }

  if (!customerId) {
    notifyLine_('🚨 customer_id未取得（ナポリ）\nメール: ' + (email || '不明') + '\nお名前: ' + name + '\nStripeダッシュボードで確認して手動対応してください。');
  }

  var sendStepUrl = customerId
    ? 'https://saas.shikisai.work/api/send-step?customer_id=' + customerId
    : '⚠️ customer_id未取得（Stripeで確認）';
  var rec = recovered ? '\n♻️ customer_idはメールから自動復元しました' : '';

  var msg =
    '📋 とうこさん【ナポリ】フォーム回答あり！\n\n' +
    'お名前：' + name + '\n' +
    'Threads ID：' + tid + rec + '\n' +
    'Instagram：' + (insta || '未記入') + '\n\n' +
    '【やること】\n' +
    'こちらをタップ ↓\n' +
    '（連携のご案内がクライアントのLINEに自動送信されます）\n' +
    sendStepUrl;
  notifyLine_(msg);
}

// ============================================================
// helpers
// ============================================================
function sec_(form, title) { form.addSectionHeaderItem().setTitle(title); }
function txt_(form, title, help, req) { form.addTextItem().setTitle(title).setHelpText(help || '').setRequired(!!req); }
function par_(form, title, help, req) { form.addParagraphTextItem().setTitle(title).setHelpText(help || '').setRequired(!!req); }
function mc_(form, title, choices, req) { form.addMultipleChoiceItem().setTitle(title).setChoiceValues(choices).setRequired(!!req); }
function chk_(form, title, choices, req) { form.addCheckboxItem().setTitle(title).setChoiceValues(choices).setRequired(!!req); }
function first_(r, k) { return ((r[k] || [''])[0] || '').trim(); }

function removeNaporiTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'onNaporiFormSubmit') ScriptApp.deleteTrigger(t);
  });
}

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
  s = s.replace(/[０-９Ａ-Ｚａ-ｚ．＿－]/g, function (c) { return map[c] || c; });
  while (s.length > 0 && (s.charAt(0) === '@' || s.charAt(0) === '＠')) s = s.substring(1);
  return s.trim().toLowerCase();
}

function notifyLine_(text) {
  var token = PropertiesService.getScriptProperties().getProperty('LINE_CHANNEL_ACCESS_TOKEN');
  if (!token) { console.error('LINE_CHANNEL_ACCESS_TOKEN not set'); return; }
  try {
    UrlFetchApp.fetch('https://api.line.me/v2/bot/message/broadcast', {
      method: 'post',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
      payload: JSON.stringify({ messages: [{ type: 'text', text: text }] }),
      muteHttpExceptions: true
    });
  } catch (err) { console.error('notifyLine_失敗:', err); }
}
