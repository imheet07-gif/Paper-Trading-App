// PaperTrader home-screen widget for the Scriptable app (iOS).
//
// Setup (once):
//  1. In PaperTrader, open Funds > "Widget token" and copy the URL + token it shows.
//  2. Run this script in Scriptable once; it asks for both and saves them in the iOS Keychain.
//  3. Add a Scriptable widget to the home screen, choose this script. Small and medium sizes work.
// To change the URL or token later, run the script inside Scriptable and choose "Reset".
//
// iOS decides how often widgets refresh (often every 15+ minutes), so this is a glance,
// not live tracking. Tap the widget to open the app.

const KEY_URL = "papertrader.url";
const KEY_TOKEN = "papertrader.token";

async function ask(title, message, placeholder, secure) {
  const a = new Alert();
  a.title = title; a.message = message;
  if (secure) a.addSecureTextField(placeholder, ""); else a.addTextField(placeholder, "");
  a.addAction("Save"); a.addCancelAction("Cancel");
  const choice = await a.present();
  return choice === -1 ? null : a.textFieldValue(0);
}

async function configure() {
  const url = await ask("PaperTrader URL", "e.g. https://your-host.example", "https://", false);
  if (!url) return false;
  const token = await ask("Widget token", "Paste the token from the app", "pw_...", true);
  if (!token) return false;
  Keychain.set(KEY_URL, url.trim().replace(/\/+$/, ""));
  Keychain.set(KEY_TOKEN, token.trim());
  return true;
}

function money(n) {
  if (n == null) return "–";
  const s = Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  return (n < 0 ? "-" : n > 0 ? "+" : "") + "₹" + s;
}
const pnlColor = n => n > 0 ? new Color("#2e9e5b") : n < 0 ? new Color("#d6453d") : Color.gray();
const FEED = { live: "Live", demo: "Simulated", stale: "Stale", down: "Feed down", starting: "Connecting", closed: "Market closed" };

async function load() {
  const req = new Request(Keychain.get(KEY_URL) + "/api/widget");
  req.headers = { Authorization: "Bearer " + Keychain.get(KEY_TOKEN) };
  req.timeoutInterval = 15;
  const data = await req.loadJSON();
  if (req.response.statusCode !== 200) throw new Error("HTTP " + req.response.statusCode);
  return data;
}

function build(d, error) {
  const w = new ListWidget();
  w.backgroundColor = Color.dynamic(new Color("#ffffff"), new Color("#1b1f27"));
  w.url = Keychain.contains(KEY_URL) ? Keychain.get(KEY_URL) : undefined;
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);

  if (error) {
    const t = w.addText("PaperTrader"); t.font = Font.boldSystemFont(13);
    const e = w.addText(error); e.font = Font.systemFont(11); e.textColor = new Color("#d6453d");
    return w;
  }

  const head = w.addStack(); head.layoutHorizontally();
  const brand = head.addText("PaperTrader"); brand.font = Font.boldSystemFont(12); brand.textColor = new Color("#ff5722");
  head.addSpacer();
  const st = head.addText(FEED[d.feed.state] || d.feed.state); st.font = Font.systemFont(10);
  st.textColor = (d.feed.state === "stale" || d.feed.state === "down") ? new Color("#d6453d") : Color.gray();

  w.addSpacer(4);
  const p = w.addText(money(d.net_pnl)); p.font = Font.boldSystemFont(24); p.textColor = pnlColor(d.net_pnl);
  const sub = w.addText("Net P&L · " + d.open_positions + " open"); sub.font = Font.systemFont(10); sub.textColor = Color.gray();

  if (config.widgetFamily !== "small") {
    w.addSpacer(6);
    for (const i of d.indices) {
      const row = w.addStack(); row.layoutHorizontally();
      const n = row.addText(i.name); n.font = Font.mediumSystemFont(11);
      row.addSpacer();
      const v = row.addText(i.spot == null ? "–" : i.spot.toLocaleString("en-IN", { maximumFractionDigits: 2 }));
      v.font = Font.systemFont(11);
    }
    for (const s of d.strategies) {
      const row = w.addStack(); row.layoutHorizontally();
      const n = row.addText(s.strategy); n.font = Font.systemFont(10); n.textColor = Color.gray();
      row.addSpacer();
      const v = row.addText(money(s.net_pnl)); v.font = Font.systemFont(10); v.textColor = pnlColor(s.net_pnl);
    }
  }
  w.addSpacer();
  const t = w.addText("Updated " + new Date(d.as_of).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }));
  t.font = Font.systemFont(9); t.textColor = Color.gray();
  return w;
}

async function main() {
  if (config.runsInWidget) {
    const ready = Keychain.contains(KEY_URL) && Keychain.contains(KEY_TOKEN);
    let widget;
    if (!ready) widget = build(null, "Open this script in Scriptable to set it up.");
    else { try { widget = build(await load()); } catch (e) { widget = build(null, "Can't reach PaperTrader (" + e.message + ")"); } }
    Script.setWidget(widget);
    Script.complete();
    return;
  }
  // Run by hand inside Scriptable: set up, reset, or preview.
  if (!Keychain.contains(KEY_URL) || !Keychain.contains(KEY_TOKEN)) { if (!(await configure())) return; }
  else {
    const m = new Alert(); m.title = "PaperTrader widget";
    m.addAction("Preview"); m.addDestructiveAction("Reset URL and token"); m.addCancelAction("Close");
    const pick = await m.present();
    if (pick === -1) return;
    if (pick === 1 && !(await configure())) return;
  }
  let widget;
  try { widget = build(await load()); } catch (e) { widget = build(null, "Can't reach PaperTrader (" + e.message + ")"); }
  await widget.presentMedium();
  Script.complete();
}
await main();
