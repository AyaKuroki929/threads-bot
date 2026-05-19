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
  var customerId = (responses['_customer_id'] || [''])[0].trim();

  var connectUrl = customerId
    ? 'https://saas.shikisai.work/api/connect?customer_id=' + customerId
    : '⚠️ customer_id未取得（Stripeダッシュボードで確認してください）';

  var message =
    '📋 とうこさん フォーム回答あり！\n\n' +
    'サロン名：' + salonName + '\n' +
    'オーナー名：' + ownerName + '\n' +
    'Threads ID：' + threadsId + '\n\n' +
    '【やること】\n' +
    '下記をLINEでコピペ送信 ↓\n' +
    '──────────────\n' +
    'Threadsとの連携手順をお送りします📱\n\n' +
    '下記URLを開いて、Instagramアカウントでログインし\n' +
    '連携を完了してください。\n\n' +
    connectUrl + '\n\n' +
    'ご不明な点はいつでもお気軽にご連絡ください😊\n' +
    '──────────────';

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
