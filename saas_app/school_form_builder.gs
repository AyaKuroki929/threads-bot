/**
 * ============================================================
 * 晶子サロンアカデミー｜Threads自動投稿 スクール用フォーム
 *   ビルダー（createSchoolForm）＋ 回答ハンドラ（onSchoolFormSubmit）
 * ------------------------------------------------------------
 * 【使い方】
 *  1) https://script.google.com → 「新しいプロジェクト」
 *  2) このファイルの中身を全部コピーして貼り付け → 保存（プロジェクト名は何でもOK）
 *  3) 上部の関数選択を「createSchoolForm」にして ▶実行 → 権限を承認
 *  4) メニュー「実行数 / 実行ログ」を開き、最後に出る
 *       PUBLISHED_URL / SPREADSHEET_ID / CUSTOMER_ID_ENTRY
 *     をコピーしてClaude（彩さん）に渡す
 *  5) このプロジェクトの「プロジェクトの設定 → スクリプト プロパティ」で
 *       LINE_CHANNEL_ACCESS_TOKEN = （とうこさん公式LINEのアクセストークン）
 *     を1つ追加する（回答時の管理者LINE通知に必要）
 *
 * ※ createSchoolForm は一度だけ実行する（再実行すると別のフォームがもう1つできる）。
 * ============================================================
 */

// ---- 作ったファイルは Drive「とうこさん」フォルダへ入れる（マイドライブ直下に散らばらせない・2026-10-13 彩さん指示）----
var TOUKO_FOLDER_ID = '1FD4CbqVOxy-U6ClM5eupbP6_IcXboSJp';
function moveToToukoFolder_(fileId) {
  var folder = DriveApp.getFolderById(TOUKO_FOLDER_ID);
  DriveApp.getFileById(fileId).moveTo(folder);
}

function createSchoolForm() {
  var intro = [
    'ご登録ありがとうございます。',
    'このシートは、アカデミーのことをシステムに深く知ってもらい、Threads（スレッズ）自動投稿の質を上げるためのシートです。',
    'AIがこの回答をもとに、先生にしか書けない投稿を毎日自動でお届けします。',
    '',
    'うまく書こうとしなくて大丈夫です。話しかけるように、思っていることをそのまま書いてください。',
    '詳しく・正直に書いていただくほど、投稿の質が上がります。',
    '',
    '※すべての質問が回答必須です。書くことが思いつかない項目は「特になし」とご記入いただければ次に進めます。',
    '※ホームページやSNSの内容と変わっている点があれば、今の実態を優先して書いてください。'
  ].join('\n');

  var form = FormApp.create('晶子サロンアカデミー｜Threads自動投稿 ヒアリングシート');
  form.setDescription(intro);
  form.setAllowResponseEdits(true);
  form.setCollectEmail(false);

  // ---- セクション0：基本情報 ----
  sec_(form, 'セクション0：基本情報');
  txt_(form, 'スクール・アカデミー名', '例：晶子サロンアカデミー', true);
  txt_(form, '講師名（投稿で使うお名前）', '例：syoko先生 / 晶子先生 / 熊本晶子', true);
  txt_(form, '投稿で自分を何と呼びますか？', '例：私 / わたし　「私が経験したのは…」のように投稿で使います', true);
  txt_(form, 'ThreadsのアカウントID（@から始まるID）', '例：@syoko.happy_salon', true);
  txt_(form, 'メールアドレス', 'ご登録・ご連絡用です', true);
  txt_(form, 'その他のSNS・HPのURL', 'Instagram / Facebook / 公式LINE / ホームページ など。なければ「特になし」', true);

  // ---- セクション1：投稿の目的（最重要）----
  sec_(form, 'セクション1：このThreadsアカウントの目的（最重要）');
  chk_(form, 'このアカウントで一番達成したいこと（複数選択可）', [
    'スクールの受講生を増やしたい',
    '公式LINEの登録を増やしたい',
    '認知を広げたい・知ってもらいたい',
    '「この先生に習いたい」という信頼を作りたい',
    '個別相談・体験への申込を増やしたい'
  ], true);
  par_(form, '目的の具体的な中身', '例：まずは公式LINEに登録してもらい、そこから無料講座→講座申込につなげたい', true);
  par_(form, '投稿を読んだサロンオーナーに最終的にどうなってほしいか', '例：投稿で気づき→公式LINE登録→無料講座を見る→繁盛サロン講座に申し込む', true);
  par_(form, '3ヶ月後にこうなっていたら成功という状態', '例：公式LINE登録が毎月◯件増える／投稿から相談DMが来る', true);

  // ---- セクション2：CTA ----
  sec_(form, 'セクション2：CTA（投稿の最後の誘導）');
  chk_(form, '投稿の最後で読者にしてほしい行動（複数選択可）', [
    '公式LINEに登録してほしい',
    'DMを送ってほしい',
    'コメントで反応してほしい',
    'ホームページ／申込ページを見てほしい',
    'プロフィールのリンクを見てほしい',
    '特に誘導はせず、まずは内容を届けたい'
  ], true);
  par_(form, 'CTAの誘導先と具体的な文言・キーワード', '例：公式LINEで「経営講座」と送ると7本の無料講座をプレゼント。誘導先URL（公式LINE / HP / 申込フォーム等）も書いてください', true);
  mc_(form, 'DM・コメントでの相談は受け付けてOKですか？', ['はい（むしろ歓迎）', '内容によってはOK', 'いいえ'], true);
  mc_(form, 'CTAを出す頻度', ['毎回出してOK', '2〜3回に1回くらい', 'たまにでいい（押し売り感を出したくない）'], true);

  // ---- セクション3：ターゲット ----
  sec_(form, 'セクション3：誰に届けたいか（ターゲット）');
  par_(form, 'どんなエステサロンオーナーに届けたいか', '経験年数／一人かスタッフ有／今の年商・月商規模／状況。例：開業3〜7年、一人サロン、頑張っているのに利益が残らない人', true);
  par_(form, 'そのオーナーが今一番抱えている悩み・痛み', '投稿の1行目のフックに使います。例：働き詰めなのに利益が残らない／値上げが怖い／物販が売れない／集客に振り回される', true);
  par_(form, 'こういう人には来てほしくないという人物像', '例：楽して儲けたい人／学ぶ気がなく丸投げしたい人。思いつかなければ「特になし」', true);

  // ---- セクション4：想い・哲学（心臓部）----
  sec_(form, 'セクション4：先生の想い・哲学（このシートの心臓部）');
  par_(form, 'なぜこのスクールを作ったか', 'ご自身の経験・転機・「これを伝えたい」と思った瞬間を、自由に書いてください', true);
  par_(form, '一番の失敗・どん底と、そこからどう立て直したか', 'うまくいった話より、失敗からの復活が一番読者に刺さります。差し支えない範囲で具体的に', true);
  par_(form, '「低単価で満席を目指すサロン」との違い（高単価で本当に結果を出すサロンとは）', '先生の考え方・信念をそのまま書いてください', true);
  par_(form, '物販・ホームケア教育を必須にする理由', 'それがないと結果が出ないと考える理由。例：サロンに来ていない日の習慣こそが結果を作る', true);
  par_(form, 'メニューの組み合わせ方で大事にしている考え方', '例：単発で売らず、組み合わせて結果が出る設計にする', true);
  par_(form, 'サロンオーナーが働き詰めにならず、時間とお金の余裕を持って働くために必要なこと', '先生がサロンオーナーに一番伝えたい「働き方・生き方」の部分', true);
  par_(form, 'サロンオーナーがよく持っている思い込み・勘違い', '例：「安くしないとお客様が来ない」「値上げしたら離れる」など。投稿の気づきネタになります', true);
  par_(form, 'サロンオーナーに一番気づいてほしいことを一言で', '一番伝えたいことを短く', true);

  // ---- セクション5：講座の中身 ----
  sec_(form, 'セクション5：スクール・講座の中身（今の実態を）');
  par_(form, '提供している講座・コースと価格', '箇条書きでOK。HPと変わっていれば今の内容を', true);
  par_(form, '一番おすすめ・一番成果が出る講座とその理由', '', true);
  par_(form, '投稿で触れてOKな講座の特徴的な仕組み', '例：全6ステップ29カリキュラム／量子力学を使ったマインド講座 など。なければ「特になし」', true);
  mc_(form, '価格を投稿に出してもOKですか？', ['はい（具体的な金額を出してOK）', 'いいえ（LINE・HPへ誘導する）', '一部のみOK（無料講座・体験など）'], true);

  // ---- セクション6：受講生の成果・声 ----
  sec_(form, 'セクション6：受講生の成果・声');
  mc_(form, '受講生・卒業生の成果や体験談を投稿に使ってもOKですか？', ['はい（匿名で使ってOK）', 'いいえ（一般的な内容のみ）'], true);
  par_(form, '印象的な受講生の成果・変化', '名前不要・数字があると強いです。例：入学時の4倍／半年で6倍／月商10万→200万超／働く時間が半分に', true);
  par_(form, '受講生からよく言われる感想・変化', '例：考え方が変わった／初めて利益が残った／休めるようになった', true);

  // ---- セクション7：文体・差別化・NG ----
  sec_(form, 'セクション7：文体・差別化・NG');
  par_(form, '他のサロン経営スクール・コンサルとの違い・強み', '例：現場を知り尽くしている／机上の空論ではなく実体験', true);
  par_(form, '受講を迷う人がよく比較する他の選択肢', '例：独学・YouTube・他の高額塾・他のコンサル', true);
  par_(form, '途中で挫折・離脱しやすい人のパターン', 'わかる範囲で。思いつかなければ「特になし」', true);
  par_(form, '先生の口癖・よく使う言葉・フレーズ', '例：「過去は消せない」「何度でもやり直せる」「痛いほど学んだ」。思いつかなければ「特になし」', true);
  par_(form, 'スクール独自のキーワード・コンセプトワード', '例：繁盛サロン／バクチ経営から脱出／集客×販売×リピート／学ぶ×達成。なければ「特になし」', true);
  mc_(form, '投稿のトーン', ['丁寧・落ち着いた（ですます調）', '親しみやすい・フレンドリー', 'プロとして言い切る・強め', '柔らかく共感ベース'], true);
  mc_(form, '絵文字をどうしますか？', ['使わない（または最小限）', '適度に使う', 'たくさん使う'], true);
  par_(form, '発信スタイルのNGライン', 'やりたくない表現・言葉づかい・雰囲気。例：強引な売り込み／他者を下げる比較／タメ口。なければ「特になし」', true);
  par_(form, '先生自身について投稿に出してOKな情報・出してほしくない情報', '例（OK）過去の失敗・家族のこと・趣味／例（NG）個人が特定される情報', true);
  par_(form, 'スクールとして言いたくないこと・NGワード', '例：「絶対」「100%」などの誇大表現。なければ「特になし」', true);

  // ---- 隠し：customer_id（自動入力欄。空でも送信可）----
  var cid = form.addTextItem()
    .setTitle('_customer_id')
    .setHelpText('※システム用の自動入力欄です。空欄のままで大丈夫です（触らずに送信してください）')
    .setRequired(false);

  // ---- 回答先スプレッドシート作成＆連携 ----
  var ss = SpreadsheetApp.create('晶子サロンアカデミー｜Threads フォーム回答');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
  moveToToukoFolder_(form.getId());
  moveToToukoFolder_(ss.getId());
  moveToToukoFolder_(ScriptApp.getScriptId()); // このスクリプト本体も同じフォルダへ

  // ---- onFormSubmit トリガー設置（このプロジェクトの onSchoolFormSubmit に紐付け）----
  removeSchoolTriggers_();
  ScriptApp.newTrigger('onSchoolFormSubmit').forForm(form).onFormSubmit().create();

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
// ============================================================
function onSchoolFormSubmit(e) {
  var r = e.namedValues;

  var email = first_(r, 'メールアドレス');
  if (!email) {
    for (var k in r) {
      var v = (r[k] || [''])[0] || '';
      if (v.indexOf('@') > 0 && v.indexOf('.') > 0) { email = v.trim(); break; }
    }
  }

  var schoolName = first_(r, 'スクール・アカデミー名') || '不明';
  var teacher    = first_(r, '講師名（投稿で使うお名前）') || '不明';
  var rawTid     = first_(r, 'ThreadsのアカウントID（@から始まるID）') || '不明';
  var tid        = normalizeThreadsId_(rawTid);
  var customerId = first_(r, '_customer_id');

  var required = ['スクール・アカデミー名', '講師名（投稿で使うお名前）', 'ThreadsのアカウントID（@から始まるID）'];
  var missing = required.filter(function (f) { return !r[f] || !r[f][0]; });
  if (missing.length > 0) {
    notifyLine_('🚨 スクールフォーム 必須項目が見つからない！\n欠落: ' + missing.join(', ') + '\nフォーム項目名が変わった可能性があります。');
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
      payload: JSON.stringify({ customer_id: customerId, email: email, expected_threads_id: tid }),
      muteHttpExceptions: true
    });
    var j = JSON.parse(resp.getContentText() || '{}');
    if (j && j.customer_id) { if (!customerId) recovered = true; customerId = j.customer_id; }
  } catch (err) { console.error('save-form失敗:', err); }

  if (!customerId) {
    notifyLine_('🚨 customer_id未取得（スクール）\nメール: ' + (email || '不明') + '\nスクール: ' + schoolName + '\nStripeダッシュボードで確認して手動対応してください。');
  }

  var sendStepUrl = customerId
    ? 'https://saas.shikisai.work/api/send-step?customer_id=' + customerId
    : '⚠️ customer_id未取得（Stripeで確認）';
  var rec = recovered ? '\n♻️ customer_idはメールから自動復元しました' : '';

  var msg =
    '🎓 とうこさん【スクール】フォーム回答あり！\n\n' +
    'スクール：' + schoolName + '\n' +
    '講師：' + teacher + '\n' +
    'Threads ID：' + tid + rec + '\n\n' +
    '【やること】\n' +
    '① MetaでThreadsテスター追加 →\n' +
    'https://developers.facebook.com/apps/1497479218824264/roles/roles/\n\n' +
    '② テスター追加後にこちらをタップ ↓\n' +
    '（タップするとSTEP1/2がクライアントのLINEに自動送信されます）\n' +
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

function removeSchoolTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'onSchoolFormSubmit') ScriptApp.deleteTrigger(t);
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

