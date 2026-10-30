/**
 * 明日のカレンダー予定を Google Chat の Webhook へ投稿する。
 *
 * 前提:
 *  - Apps Script ランタイムは V8（テンプレートリテラル / アロー関数を使用）
 *  - スクリプトのタイムゾーンは Asia/Tokyo（appsscript.json の timeZone）
 *  - 初回実行時に Calendar / Gmail(読み取り) / 外部通信 の承認が必要
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

// 関連メール検索の設定
const GMAIL_SEARCH_DAYS = 14;      // 何日前までのメールを探すか
const MAX_MAIL_PER_EVENT = 3;      // 1予定あたりに表示する最大スレッド数
const MAX_GUESTS_IN_QUERY = 5;     // 検索条件に含める出席者アドレスの上限
const MAIL_SUBJECT_MAX = 60;       // 件名の表示上限文字数

// 勉強会・セミナー案内メールの設定（部署全体が対象で、予定には紐づかないもの）
const SEMINAR_KEYWORDS = ["勉強会", "セミナー", "研修", "ウェビナー", "説明会", "講演会"];
const SEMINAR_SENDERS = [];        // 案内元を絞る場合に設定（例: ["jinji@example.co.jp"]）
const SEMINAR_LABEL = "";          // ラベルで絞る場合に設定（例: "案内"）
const SEMINAR_SEARCH_DAYS = 14;    // 何日前までの案内を拾うか
const MAX_SEMINAR_MAILS = 5;       // 表示する最大スレッド数

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

  // 予定の関連メールとして出したスレッドを、案内欄で重複表示しないよう記録する
  const seenThreadIds = [];

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

      const relatedMails = findRelatedMails(event, seenThreadIds);
      if (relatedMails.length > 0) {
        messageText += "  ・関連メール:\n";
        relatedMails.forEach(mail => {
          messageText += `    - ${mail}\n`;
        });
      }
      messageText += "\n";
    });

    messageText += "※事前準備や会議間の移動バッファをご確認ください。";
  }

  // 勉強会・セミナーの案内（部署全体が対象のため予定の有無にかかわらず載せる）
  const seminarMails = findSeminarMails(seenThreadIds);
  if (seminarMails.length > 0) {
    messageText += "\n\n*【勉強会・セミナーのご案内】*\n";
    seminarMails.forEach(mail => {
      messageText += `  - ${mail}\n`;
    });
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
 * 予定に関連する受信メールを探し、表示用の1行テキストの配列を返す。
 * 検索できない場合は空配列を返す（配信そのものは止めない）。
 */
function findRelatedMails(event, seenThreadIds) {
  const query = buildMailQuery(event);
  if (!query) {
    return [];
  }

  try {
    return GmailApp.search(query, 0, MAX_MAIL_PER_EVENT).map(thread => {
      seenThreadIds.push(thread.getId());
      return formatMailLine(thread);
    });
  } catch (error) {
    console.warn(`関連メールを検索できませんでした (${error.message})。メールなしで配信します。`);
    return [];
  }
}

/**
 * 勉強会・セミナーの案内メールを探し、表示用の1行テキストの配列を返す。
 * すでに予定の関連メールとして出したスレッドは除く。
 */
function findSeminarMails(seenThreadIds) {
  const query = buildSeminarQuery();
  if (!query) {
    return [];
  }

  try {
    // 重複を除いたうえで上限件数に収めたいので、少し多めに取得する
    return GmailApp.search(query, 0, MAX_SEMINAR_MAILS + seenThreadIds.length)
      .filter(thread => seenThreadIds.indexOf(thread.getId()) === -1)
      .slice(0, MAX_SEMINAR_MAILS)
      .map(thread => formatMailLine(thread));
  } catch (error) {
    console.warn(`案内メールを検索できませんでした (${error.message})。案内欄なしで配信します。`);
    return [];
  }
}

/**
 * 勉強会・セミナー案内メールの検索クエリを組み立てる。
 * 条件が1つも設定されていない場合は空文字を返す。
 */
function buildSeminarQuery() {
  const conditions = [];

  const keywords = SEMINAR_KEYWORDS
    .map(keyword => sanitizeForGmailQuery(keyword))
    .filter(keyword => keyword.length > 0);
  if (keywords.length > 0) {
    conditions.push(`subject:(${keywords.map(keyword => `"${keyword}"`).join(" OR ")})`);
  }

  if (SEMINAR_SENDERS.length > 0) {
    conditions.push(`from:(${SEMINAR_SENDERS.join(" OR ")})`);
  }

  const label = sanitizeForGmailQuery(SEMINAR_LABEL);
  if (label) {
    conditions.push(`label:"${label}"`);
  }

  if (conditions.length === 0) {
    return "";
  }
  return `(${conditions.join(" OR ")}) newer_than:${SEMINAR_SEARCH_DAYS}d -in:chats -in:drafts`;
}

/**
 * スレッドを「日付 差出人: 件名」の1行に整形する。
 */
function formatMailLine(thread) {
  const messages = thread.getMessages();
  const lastMessage = messages[messages.length - 1];
  const dateStr = Utilities.formatDate(thread.getLastMessageDate(), TIME_ZONE, "MM/dd");
  const sender = extractSenderName(lastMessage.getFrom());
  const subject = truncate(thread.getFirstMessageSubject() || "(件名なし)", MAIL_SUBJECT_MAX);
  return `${dateStr} ${sender}: ${subject}`;
}

/**
 * 予定の件名と出席者から Gmail の検索クエリを組み立てる。
 * 条件が作れない場合は空文字を返す。
 */
function buildMailQuery(event) {
  const conditions = [];

  const title = sanitizeForGmailQuery(event.getTitle() || "");
  // 2文字以下の件名は一致が広がりすぎるので条件に使わない
  if (title.length >= 3) {
    conditions.push(`subject:"${title}"`);
  }

  const guests = getGuestEmails(event);
  if (guests.length > 0) {
    conditions.push(`from:(${guests.join(" OR ")})`);
  }

  if (conditions.length === 0) {
    return "";
  }
  return `(${conditions.join(" OR ")}) newer_than:${GMAIL_SEARCH_DAYS}d -in:chats -in:drafts`;
}

/**
 * 出席者のメールアドレスを返す。自分と会議室などのリソースは除く。
 */
function getGuestEmails(event) {
  try {
    const myEmail = (Session.getActiveUser().getEmail() || "").toLowerCase();
    return event
      .getGuestList()
      .map(guest => guest.getEmail())
      .filter(email => {
        if (!email) {
          return false;
        }
        const lower = email.toLowerCase();
        // 会議室などのリソースは差出人になり得ないため除外
        return lower !== myEmail && lower.indexOf("resource.calendar.google.com") === -1;
      })
      .slice(0, MAX_GUESTS_IN_QUERY);
  } catch (error) {
    console.warn(`出席者を取得できませんでした (${error.message})。件名のみで検索します。`);
    return [];
  }
}

/**
 * Gmail の検索演算子として解釈される文字を落とし、フレーズ検索に使える文字列にする。
 */
function sanitizeForGmailQuery(text) {
  return text
    .replace(/["\\(){}]/g, " ")
    .replace(/[:]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * "表示名 <addr@example.com>" から表示名（なければアドレス）を取り出す。
 */
function extractSenderName(from) {
  if (!from) {
    return "(不明)";
  }
  const matched = from.match(/^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/);
  if (matched) {
    const displayName = matched[1].trim();
    return displayName || matched[2].trim();
  }
  return from.trim();
}

function truncate(text, maxLength) {
  return text.length > maxLength ? `${text.substring(0, maxLength)}…` : text;
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
