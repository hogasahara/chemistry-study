/*
 * 学習進捗レポート送信モジュール
 *
 * 学習の実施状況を Google Apps Script(GAS)経由でスプレッドシートに記録し、
 * 保護者へ日次メールで通知するための「サイト側」。
 *
 * - GAS は CORS ヘッダを返さないため mode:"no-cors" で送る。
 *   レスポンスは読めないので「送信成功」は確認できない前提で設計している。
 * - オフライン時(通学中など)は localStorage のキューに積み、
 *   次回ページ読み込み時(オンライン時)に順に再送する。
 * - 公開ページなので、送るテキストに個人が特定できる情報を含めないこと。
 * - トークンは公開JSに露出するが、GAS側の仕様上やむを得ず、了解済み。
 *
 * 使い方(各ページで <script src="js/report.js"></script> を読み込む):
 *   CHReport.reportAccess()                                   トップページ: 1日1回「アクセス」
 *   CHReport.reportDrillDone({name, correct, total, minutes})  ドリル完了
 *   CHReport.report(text)                                     任意テキスト
 *
 * 英語版(EAReport)からの変更点: localStorageキーを ch_ に変更 / WPMを削除 /
 * 送信テキストに「化学 」接頭辞 / GAS_URL が空の間は何も送らない(キューにも積まない)
 */
(function (global) {
  "use strict";

  // 未設定(空)の間は送信しない。英語版のGASを使い回すか新設するかは保護者に確認中(profile.md 未確認事項)
  var GAS_URL = "";
  var REPORT_TOKEN = "";
  // 英語版と同じGASに送る場合でも区別できるよう、送信テキストの先頭に付ける
  var PREFIX = "化学 ";

  var QUEUE_KEY = "ch_report_queue_v1";
  var ACCESS_KEY = "ch_report_access_date_v1";
  var QUEUE_MAX = 100;

  // ---- localStorage ヘルパー(プライベートモード等で使えない場合は黙って諦める) ----
  function lsGet(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function lsSet(key, value) {
    try { localStorage.setItem(key, value); return true; } catch (e) { return false; }
  }

  function loadQueue() {
    try {
      var q = JSON.parse(lsGet(QUEUE_KEY) || "[]");
      return Array.isArray(q) ? q : [];
    } catch (e) { return []; }
  }
  function saveQueue(queue) {
    lsSet(QUEUE_KEY, JSON.stringify(queue.slice(-QUEUE_MAX)));
  }
  function enqueue(text, ts) {
    var queue = loadQueue();
    queue.push({ ts: ts || Date.now(), text: text });
    saveQueue(queue);
  }

  // ---- 日付ユーティリティ(JST固定) ----
  function jstDate(ts) {
    // UTC+9 にずらしてから UTC 系の getter で読む
    return new Date((ts || Date.now()) + 9 * 60 * 60 * 1000);
  }
  function todayJst() {
    return jstDate().toISOString().slice(0, 10); // YYYY-MM-DD
  }
  function stampJst(ts) {
    var d = jstDate(ts);
    return (d.getUTCMonth() + 1) + "/" + d.getUTCDate() + " " +
      d.getUTCHours() + ":" + ("0" + d.getUTCMinutes()).slice(-2);
  }

  // ---- 送信 ----
  function isOnline() {
    return typeof navigator === "undefined" || navigator.onLine !== false;
  }

  // fetch を投げる。例外(ネットワーク断など)のときだけ reject する。
  // keepalive: 送信直後にページ遷移しても(結果→記録ページ など)リクエストを完了させる。
  function send(text) {
    try {
      return fetch(GAS_URL, {
        method: "POST",
        mode: "no-cors",
        keepalive: true,
        headers: { "Content-Type": "text/plain" },
        body: JSON.stringify({ token: REPORT_TOKEN, text: text })
      });
    } catch (e) {
      return Promise.reject(e);
    }
  }

  // 任意テキストを送る。オフライン or 送信例外ならキューに積む。
  function report(text) {
    text = String(text || "").trim();
    if (!text || !GAS_URL) return Promise.resolve(false);
    text = PREFIX + text;
    var ts = Date.now();
    if (!isOnline()) {
      enqueue(text, ts);
      return Promise.resolve(false);
    }
    return send(text).then(function () { return true; }, function () {
      enqueue(text, ts);
      return false;
    });
  }

  // キューを先頭から順に再送する。失敗したものだけキューに残す。
  var flushing = false;
  function flushQueue() {
    if (flushing || !GAS_URL || !isOnline()) return Promise.resolve();
    var queue = loadQueue();
    if (!queue.length) return Promise.resolve();
    flushing = true;
    saveQueue([]);
    var remaining = [];
    var chain = Promise.resolve();
    queue.forEach(function (item) {
      chain = chain.then(function () {
        return send("[" + stampJst(item.ts) + "発生] " + item.text).then(null, function () {
          remaining.push(item);
        });
      });
    });
    return chain.then(function () {
      // 失敗分は、再送中に新たに積まれた分より前に、元の順序で戻す
      saveQueue(remaining.concat(loadQueue()));
      flushing = false;
    });
  }

  // ---- 用途別 ----

  // トップページ用: 同じ日(JST)には1回だけ「アクセス」を送る
  function reportAccess() {
    if (!GAS_URL) return Promise.resolve(false);
    var today = todayJst();
    if (lsGet(ACCESS_KEY) === today) return Promise.resolve(false);
    // localStorage が使えない環境では毎回送ってしまうため、保存できたときだけ送る
    if (!lsSet(ACCESS_KEY, today)) return Promise.resolve(false);
    return report("アクセス");
  }

  // ドリル完了用。書式: 終了 <ドリル名> 正答率<数値>% 所要<分>分
  // 存在しない項目(correct,total / minutes)は省略される。
  function formatDrillDone(r) {
    var parts = ["終了", String(r.name || "ドリル").replace(/\s+/g, " ").trim()];
    if (isFinite(r.correct) && isFinite(r.total) && r.total > 0) {
      parts.push("正答率" + Math.round(r.correct / r.total * 100) + "%");
    }
    if (isFinite(r.minutes) && r.minutes > 0) parts.push("所要" + Math.max(1, Math.round(r.minutes)) + "分");
    return parts.join(" ");
  }
  function reportDrillDone(r) {
    return report(formatDrillDone(r || {}));
  }

  global.CHReport = {
    report: report,
    reportAccess: reportAccess,
    reportDrillDone: reportDrillDone,
    formatDrillDone: formatDrillDone,
    flushQueue: flushQueue,
    todayJst: todayJst
  };

  // ページ読み込み時にオフライン分を再送。オンライン復帰時にも試みる。
  flushQueue();
  if (typeof window !== "undefined" && window.addEventListener) {
    window.addEventListener("online", function () { flushQueue(); });
  }
})(this);
