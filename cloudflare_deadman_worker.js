/**
 * Dead Man's Switch — LINE通知ワーカー
 *
 * healthchecks.io からの "down" webhook を受け取り、LINE に通知する。
 * Cloudflare Dashboard にそのまま貼り付けて使う。
 *
 * 必要な Worker シークレット（Settings > Variables > Secrets）:
 *   LINE_CHANNEL_ACCESS_TOKEN  — LINE Channel Access Token（Claude通知Bot）
 *   NOTIFY_SECRET              — 任意のランダム文字列（healthchecks.io側のヘッダーと揃える）
 */

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') {
      return new Response('Method Not Allowed', { status: 405 });
    }

    const secret = request.headers.get('X-Notify-Secret') || '';
    if (!env.NOTIFY_SECRET || secret !== env.NOTIFY_SECRET) {
      return new Response('Unauthorized', { status: 401 });
    }

    let checkName = 'スロット不明';
    try {
      const body = await request.json();
      checkName = body.name || checkName;
    } catch {}

    const text =
      `⚠️ GH Actions 障害検知\n\n` +
      `「${checkName}」が予定時刻から40分以上経過してもpingが届いていません。\n\n` +
      `GitHub障害の可能性があります👇\n` +
      `https://www.githubstatus.com/\n\n` +
      `Actions確認👇\n` +
      `https://github.com/AyaKuroki929/threads-bot/actions`;

    await fetch('https://api.line.me/v2/bot/message/broadcast', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ messages: [{ type: 'text', text }] }),
    });

    return new Response('OK', { status: 200 });
  },
};
