/**
 * 明日のカレンダー予定を Google Chat の Webhook へ投稿する。
 *
 * 前提:
 *  - Apps Script ランタイムは V8（テンプレートリテラル / アロー関数を使用）
 *  - スクリプトのタイムゾーンは Asia/Tokyo（appsscript.json の timeZone）
 *
 * 注意: このリポジトリは public なので、CHAT_WEBHOOK_URL は
 * プレースホルダのままにしてある。実際の key / token を含む URL は
 * Apps Script プロジェクト側にだけ記述する（ここには commit しない）。
 */

// Google ChatのWebhook URL
const CHAT_WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/SPACE_ID/messages?key=YOUR_KEY&token=YOUR_TOKEN";

const TIME_ZONE = "Asia/Tokyo";

// 日本の祝日カレンダー（Google が提供する公開カレンダー）
const HOLIDAY_CALENDAR_ID = "ja.japanese#holiday@group.v.calendar.google.com";

// 祝日カレンダーに入っているが休業日ではない行事名（誤スキップが起きたらここに追加する）
// 例: ["クリスマス", "大晦日"]
const NON_HOLIDAY_TITLES = [];
// Google Chat の text は 4096 文字まで
const CHAT_TEXT_LIMIT = 4096;

function sendTomorrowScheduleToChat() {
  const now = new Date();

  // 明日 00:00:00 以上、明後日 00:00:00 未満（終了は排他にして取りこぼしを防ぐ）
  const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const endOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 2);

  // 翌日が休業日（土・日・祝）なら配信しない
  const skipReason = findSkipReason(startOfDay, endOfDay);
  if (skipReason) {
    console.log(`${Utilities.formatDate(startOfDay, TIME_ZONE, "yyyy/MM/dd")} は${skipReason}のため配信をスキップしました。`);
    return;
  }

  const calendar = CalendarApp.getDefaultCalendar();
  const events = calendar.getEvents(startOfDay, endOfDay);

  const dateStr = Utilities.formatDate(startOfDay, TIME_ZONE, "yyyy/MM/dd (E)");
  let messageText = `*【明日の予定共有】 (${dateStr})*\nお疲れ様です！明日のスケジュールを共有します。\n\n`;

  if (events.length === 0) {
    messageText += "明日の予定はありません。ゆっくりお休みいただくか、集中作業に充てられます！";
  } else {
    // 終日イベントを先に、その後は開始時刻順
    events.sort((a, b) => {
      const allDayDiff = (a.isAllDayEvent() ? 0 : 1) - (b.isAllDayEvent() ? 0 : 1);
      if (allDayDiff !== 0) {
        return allDayDiff;
      }
      return a.getStartTime().getTime() - b.getStartTime().getTime();
    });

    events.forEach((event, index) => {
      const title = event.getTitle() || "(タイトルなし)";
      let timeStr;

      if (event.isAllDayEvent()) {
        timeStr = "終日";
      } else {
        const start = Utilities.formatDate(event.getStartTime(), TIME_ZONE, "HH:mm");
        const end = Utilities.formatDate(event.getEndTime(), TIME_ZONE, "HH:mm");
        timeStr = `${start} - ${end}`;
      }

      messageText += `*${index + 1}. [${timeStr}] ${title}*\n`;

      const location = event.getLocation();
      if (location) {
        messageText += `  ・場所: ${location}\n`;
      }

      const shortDesc = buildShortDescription(event.getDescription());
      if (shortDesc) {
        messageText += `  ・アジェンダ概要: ${shortDesc}\n`;
      }
      messageText += "\n";
    });

    messageText += "※事前準備や会議間の移動バッファをご確認ください。";
  }

  postToChat(messageText);
}

/**
 * 対象日が休業日なら理由の文字列を、営業日なら空文字を返す。
 */
function findSkipReason(startOfDay, endOfDay) {
  const dayOfWeek = startOfDay.getDay(); // 0:日曜 ... 6:土曜
  if (dayOfWeek === 6) {
    return "土曜日";
  }
  if (dayOfWeek === 0) {
    return "日曜日";
  }

  const holidayName = findHolidayName(startOfDay, endOfDay);
  if (holidayName) {
    return `祝日 (${holidayName})`;
  }
  return "";
}

/**
 * 対象日の祝日名を返す。祝日でなければ空文字。
 * カレンダーを参照できない場合は、配信を止めないよう空文字を返す（フェイルオープン）。
 */
function findHolidayName(startOfDay, endOfDay) {
  try {
    const holidayCalendar = CalendarApp.getCalendarById(HOLIDAY_CALENDAR_ID);
    if (!holidayCalendar) {
      console.warn("祝日カレンダーを取得できませんでした。祝日判定をスキップして配信します。");
      return "";
    }

    const names = holidayCalendar
      .getEvents(startOfDay, endOfDay)
      .map(event => (event.getTitle() || "").trim())
      .filter(title => title && NON_HOLIDAY_TITLES.indexOf(title) === -1);

    return names.length > 0 ? names[0] : "";
  } catch (error) {
    console.warn(`祝日カレンダーを参照できませんでした (${error.message})。祝日判定をスキップして配信します。`);
    return "";
  }
}

/**
 * 予定の説明文を1行の概要テキストに整形する。概要が取れない場合は空文字を返す。
 */
function buildShortDescription(description) {
  if (!description) {
    return "";
  }

  const plain = description
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|li|tr)>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/gi, "&");

  const firstLine = plain
    .split("\n")
    .map(line => line.trim())
    .filter(line => line.length > 0)[0];

  if (!firstLine) {
    return "";
  }

  const maxLength = 80;
  // 実際に切り詰めたときだけ省略記号を付ける
  return firstLine.length > maxLength ? `${firstLine.substring(0, maxLength)}…` : firstLine;
}

/**
 * Google Chat へ投稿する。4096 文字を超える場合は分割して送る。
 */
function postToChat(messageText) {
  splitForChat(messageText).forEach(chunk => {
    const options = {
      method: "post",
      contentType: "application/json; charset=UTF-8",
      payload: JSON.stringify({ text: chunk }),
      muteHttpExceptions: true
    };

    const response = UrlFetchApp.fetch(CHAT_WEBHOOK_URL, options);
    const code = response.getResponseCode();
    if (code < 200 || code >= 300) {
      // muteHttpExceptions: true のままだと失敗に気付けないので明示的に失敗させる
      throw new Error(`Google Chat への投稿に失敗しました (HTTP ${code}): ${response.getContentText()}`);
    }
  });
}

/**
 * Chat の文字数上限に収まるよう、行単位で分割する。
 */
function splitForChat(messageText) {
  if (messageText.length <= CHAT_TEXT_LIMIT) {
    return [messageText];
  }

  const chunks = [];
  let current = "";

  messageText.split("\n").forEach(line => {
    // 1行だけで上限を超える場合は、その行を強制的に切る
    let rest = line;
    while (rest.length > CHAT_TEXT_LIMIT) {
      if (current) {
        chunks.push(current);
        current = "";
      }
      chunks.push(rest.substring(0, CHAT_TEXT_LIMIT));
      rest = rest.substring(CHAT_TEXT_LIMIT);
    }

    const candidate = current ? `${current}\n${rest}` : rest;
    if (candidate.length > CHAT_TEXT_LIMIT) {
      chunks.push(current);
      current = rest;
    } else {
      current = candidate;
    }
  });

  if (current) {
    chunks.push(current);
  }
  return chunks;
}
