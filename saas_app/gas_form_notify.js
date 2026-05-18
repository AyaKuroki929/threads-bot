/**
 * とうこさんSaaS - Googleフォーム送信時にLINE通知
 * スクリプトプロパティに以下を設定すること:
 *   LINE_CHANNEL_ACCESS_TOKEN : Claude通知botのチャネルアクセストークン
 */

function onFormSubmit(e) {
  var responses = e.namedValues;

  var salonName  = (responses['サロン名'] || ['不明'])[0].trim();
  var ownerName  = (responses['オーナー名（投稿で使うお名前）'] || ['不明'])[0].trim();
  var threadsId  = (responses['Threadsのアカウント名（@から始まるID）'] || ['不明'])[0].trim();

  var message =
    '📋 とうこさん フォーム回答あり！\n\n' +
    'サロン名：' + salonName + '\n' +
    'オーナー名：' + ownerName + '\n' +
    'Threads ID：' + threadsId + '\n\n' +
    '▼ 次のステップ\n' +
    '⑤ MetaでThreadsテスター追加：\n' +
    'https://developers.facebook.com/apps/1497479218824264/roles/roles/\n\n' +
    '⑥ connect URLをLINEで送信（Stripeの通知メッセージにあります）';

  var token = PropertiesService.getScriptProperties().getProperty('LINE_CHANNEL_ACCESS_TOKEN');

  if (!token) {
    console.error('LINE_CHANNEL_ACCESS_TOKEN not set in script properties');
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
